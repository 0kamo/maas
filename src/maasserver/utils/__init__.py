# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Utilities."""

__all__ = [
    'absolute_reverse',
    'absolute_reverse_url',
    'build_absolute_uri',
    'find_rack_controller',
    'get_local_cluster_UUID',
    'ignore_unused',
    'make_validation_error_message',
    'strip_domain',
    'synchronised',
    ]

from functools import wraps
from urllib.parse import (
    urlencode,
    urljoin,
    urlparse,
)

from django.core.urlresolvers import reverse
from maasserver.config import RegionConfiguration
from provisioningserver.config import (
    ClusterConfiguration,
    UUID_NOT_SET,
)
from provisioningserver.utils.text import make_bullet_list


def ignore_unused(*args):
    """Suppress warnings about unused variables.

    This function does nothing.  Use it whenever you have deliberately
    unused symbols: pass them to this function and lint checkers will no
    longer consider them unused.
    """


def absolute_reverse(view_name, query=None, base_url=None, *args, **kwargs):
    """Return the absolute URL (i.e. including the URL scheme specifier and
    the network location of the MAAS server).  Internally this method simply
    calls Django's 'reverse' method and prefixes the result of that call with
    the configured MAAS URL.

    Consult the 'maas-region local_config_set --default-url' command for
    details on how to set the MAAS URL.

    :param view_name: Django's view function name/reference or URL pattern
        name for which to compute the absolute URL.
    :param query: Optional query argument which will be passed down to
        urllib.urlencode.  The result of that call will be appended to the
        resulting url.
    :param base_url: Optional url used as base.  If None is provided, then
        configured MAAS URL will be used.
    :param args: Positional arguments for Django's 'reverse' method.
    :param kwargs: Named arguments for Django's 'reverse' method.

    """
    if not base_url:
        with RegionConfiguration.open() as config:
            base_url = config.maas_url
    url = urljoin(base_url, reverse(view_name, *args, **kwargs))
    if query is not None:
        url += '?%s' % urlencode(query, doseq=True)
    return url


def absolute_url_reverse(view_name, query=None, *args, **kwargs):
    """Returns the absolute path (i.e. starting with '/') for the given view.

    This utility is meant to be used by methods that need to compute URLs but
    run outside of Django and thus don't have the 'script prefix' transparently
    added the the URL.

    :param view_name: Django's view function name/reference or URL pattern
        name for which to compute the absolute URL.
    :param query: Optional query argument which will be passed down to
        urllib.urlencode.  The result of that call will be appended to the
        resulting url.
    :param args: Positional arguments for Django's 'reverse' method.
    :param kwargs: Named arguments for Django's 'reverse' method.
    """
    with RegionConfiguration.open() as config:
        abs_path = urlparse(config.maas_url).path
    if not abs_path.endswith('/'):
        # Add trailing '/' to get urljoin to behave.
        abs_path = abs_path + '/'
    # Force prefix to be '' so that Django doesn't use the 'script prefix' (
    # which might be there or not depending on whether or not the thread local
    # variable has been initialized).
    reverse_link = reverse(view_name, prefix='', *args, **kwargs)
    if reverse_link.startswith('/'):
        # Drop the leading '/'.
        reverse_link = reverse_link[1:]
    url = urljoin(abs_path, reverse_link)
    if query is not None:
        url += '?%s' % urlencode(query, doseq=True)
    return url


def build_absolute_uri(request, path):
    """Return absolute URI corresponding to given absolute path.

    :param request: An http request to the API.  This is needed in order to
        figure out how the client is used to addressing
        the API on the network.
    :param path: The absolute http path to a given resource.
    :return: Full, absolute URI to the resource, taking its networking
        portion from `request` but the rest from `path`.
    """
    scheme = "https" if request.is_secure() else "http"
    return "%s://%s%s" % (scheme, request.get_host(), path)


def strip_domain(hostname):
    """Return `hostname` with the domain part removed."""
    return hostname.split('.', 1)[0]


def get_local_cluster_UUID():
    """Return the UUID of the local cluster (or None if it cannot be found)."""
    with ClusterConfiguration.open() as config:
        if config.cluster_uuid == UUID_NOT_SET:
            return None
        else:
            return config.cluster_uuid


def find_rack_controller(request):
    """Find the rack controller whose managing the subnet that contains the
    requester's address.

    There may be multiple matching rack controllers, but we choose the active
    rack controller for that subnet.
    """
    # Circular imports.
    from maasserver.models.subnet import Subnet
    ip_address = request.META['REMOTE_ADDR']
    if ip_address is None:
        return None

    subnet = Subnet.objects.get_best_subnet_for_ip(ip_address)
    if subnet is None:
        return None
    if subnet.vlan.dhcp_on is False:
        return None
    return subnet.vlan.primary_rack


def synchronised(lock):
    """Decorator to synchronise a call against a given lock.

    Note: if the function being wrapped is a generator, the lock will
    *not* be held for the lifetime of the generator; to this decorator,
    it looks like the wrapped function has returned.
    """
    def synchronise(func):
        @wraps(func)
        def call_with_lock(*args, **kwargs):
            with lock:
                return func(*args, **kwargs)
        return call_with_lock
    return synchronise


def gen_validation_error_messages(error):
    """Return massaged messages from a :py:class:`ValidationError`."""
    message_dict = error.message_dict
    for field in sorted(message_dict):
        field_messages = message_dict[field]
        if field == "__all__":
            for field_message in field_messages:
                yield field_message
        else:
            for field_message in field_messages:
                yield "%s: %s" % (field, field_message)


def make_validation_error_message(error):
    """Return a massaged message from a :py:class:`ValidationError`.

    The message takes the form of a textual bullet-list.
    """
    return make_bullet_list(gen_validation_error_messages(error))
