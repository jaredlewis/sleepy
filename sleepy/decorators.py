"""
Sleepy Decorators

Decorators that implement "guards" or functions that wrap around API methods to
handle preconditions before functions are called. For example "in order to call
this method the user should be authenticated", "in order to call this method
you must pass this parameter" etc.

:author: Adam Haney
:organization: Retickr
:contact: adam.haney@retickr.com
:license: Copyright (c) 2011 retickr, LLC
"""

__author__ = "Adam Haney <adam.haney@retickr.com>"
__license__ = "Copyright (c) 2011 retickr, LLC"


import pycassa
import MySQLdb
import MySQLdb.cursors
import time
import base64
import json


class RequiresAuthentication(object):
    """
    Requires Authentication

    This decorator checks Cassandra for the
    users["<username>"]["Authentication"]["PassHash"] value it then compares
    this with the hash provided by the child class of the password that is
    passed in. If this fails, it prints an error, if it succeeds it calls the
    function it is decorating. Since it's already pulled from Cassandra it
    goes ahead and pulls out the user's UserId and stores it in self.user_id
    we can take advantage of this extra information in many member functions.


    Authentication Methods
    ----------------------

    Currently it supports 3 different methods of
    authentication. A user may either pass their username as a GET, POST or PUT
    or DELETE variable (or for that matter a variable for any REQUEST type) or
    when the url pattern supports it they may pass their username as part of
    the url. We also support HTTP Basic Authentication as discussed in RFC 1945
    and the username may be passed this way. Passwords can be passed in as
    REQUEST parameters in plain text (always use HTTPS), the pass_hash may be
    passed in, or we can use HTTP basic auth. Please note that in cases where
    ther username is passed in in multiple ways the usernames must match.
    """
    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.args = *args
        self.kwargs = *kwargs

    def _authenticate(self, username, password):
        # Make sure we're connected to users_cf
        if not hasattr(self, 'column_family') or self.column_family != 'users':
            setattr(self, "%s_cf" % 'users',
                    pycassa.ColumnFamily(self.cass_pool, 'users'))

        # Get user information
        try:
            user = self.users_cf.get(
                username,
                read_consistency_level=pycassa.ConsistencyLevel.QUORUM)

        except pycassa.NotFoundException:
            return self.json_err("This user does not exist",
                                 "Invalid Username", error_code=401)

        # Compare hashes
        if user["Authentication"]["PassHash"] != self.user_passhash:
            return self.json_err('Password Incorrect',
                                 'Invalid Password',
                                 error_code=401)

        self.user_id = int(user["Information"]["UserId"])

        # Go ahead and store the user info, it reduces the number
        # of requests to Cassandra
        self.user_info = user

        # Store the last access time for every user so throttling
        # functions can use this info
        self.users_cf.insert(
            self.username,
            {
                "Information":
                    {
                    "LastApiRequest": str(time.time())
                    }
                }
            )
        

    def __call__(self, fn):
        request = self.request
        *args = self.args
        **kwargs = self.kwargs

        # Get ther user_passhash, either as a parameter, from HTTP basic
        # Authorization or hash it from a password
        self.user_passhash = None
        header_username = None

        if "password" in request.REQUEST:
            self.user_passhash = self.hash(request.REQUEST["password"])

        elif "passhash" in request.REQUEST:
            self.user_passhash = request.REQUEST["passhash"]

        elif "HTTP_AUTHORIZATION" in request.META:

            # Attempt to parse the Authorization header
            try:
                auth_header = request.META['HTTP_AUTHORIZATION']

                # Get the authorization token and base 64 decode it
                auth_string = base64.b64decode(auth_header.split(' ')[1])

                # Grab the username and password from the auth_string
                password = auth_string.split(':')[1]
                header_username = auth_string.split(':')[0]

                self.user_passhash = self.hash(password)

            # The authorization string didn't comply to the standard
            except KeyError:
                return self.json_err(
                    "The Authorization header that you passed does not comply"
                    + "with the RFC 1945 HTTP basic authentication standard "
                    + "(http://tools.ietf.org/html/rfc1945) you passed "
                    + "{0}".format(auth_header))

        else:
            return self.json_err(
                "You must provide a password, passhash or use HTTP Basic Auth",
                'Authentication Error',
                error_code=401)

        # Get username there are several ways they could pass this information
        username = None
        if "username" in self.kwargs:
            username = self.kwargs["username"]

        elif "username" in request.REQUEST:
            username = str(request.REQUEST["username"])

        elif None != header_username:
            username = header_username

        else:
            return self.json_err(
                'You must provide a username parameter',
                'Authentication Error',
                error_code=401)

        # If we've passed the username in two places make sure that they match
        if header_username != None and username != header_username:
            return self.json_err(
                "You've passed the username in the HTTP Authorization"
                    + " header and as a parameter they don't match",
                "Parameter Error")

        return fn(self, request, *args, **kwargs)
    return _check


def RequiresParameter(param):
    """
    This is decorator that makes sure the function it wraps has received a
    given parameter in the request.REQUEST object. If the wrapped function
    did not receive this parameter it throws a django response containing
    an error and bails out
    """
    def _wrap(fn):
        def _check(self, request, *args, **kwargs):
            if param in request.REQUEST:
                return fn(self, request)
            else:
                return self.json_err(
                    "{0} reqs to {1} should contain the {2} parameter".format(
                        fn.__name__,
                        self.__class__.__name__,
                        param
                        )
                    )
        return _check
    return _wrap
