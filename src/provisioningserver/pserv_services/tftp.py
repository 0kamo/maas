# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Twisted Application Plugin for the MAAS TFTP server."""

__all__ = [
    "TFTPBackend",
    "TFTPService",
    ]

from functools import partial
import http.client
import json
from socket import (
    AF_INET,
    AF_INET6,
)
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlparse,
)

from apiclient.utils import ascii_url
from netaddr import IPAddress
from provisioningserver.boot import (
    BootMethodRegistry,
    get_remote_mac,
)
from provisioningserver.drivers import ArchitectureRegistry
from provisioningserver.events import (
    EVENT_TYPES,
    send_event_node_mac_address,
)
from provisioningserver.kernel_opts import KernelParameters
from provisioningserver.utils import (
    tftp,
    typed,
)
from provisioningserver.utils.network import get_all_interface_addresses
from provisioningserver.utils.tftp import TFTPPath
from provisioningserver.utils.twisted import (
    deferred,
    PageFetcher,
)
from tftp.backend import FilesystemSynchronousBackend
from tftp.errors import (
    BackendError,
    FileNotFound,
)
from tftp.protocol import TFTP
from twisted.application import internet
from twisted.application.service import MultiService
from twisted.internet import (
    reactor,
    udp,
)
from twisted.internet.abstract import isIPv6Address
from twisted.internet.address import (
    IPv4Address,
    IPv6Address,
)
from twisted.internet.defer import (
    inlineCallbacks,
    maybeDeferred,
    returnValue,
)
from twisted.internet.task import deferLater
from twisted.python import log
from twisted.python.filepath import FilePath
import twisted.web.error


def log_request(mac_address, file_name, clock=reactor):
    """Log a TFTP request.

    This will be logged at a later iteration of the `clock` so as to not delay
    the task currently in progress.
    """
    # If the file name is a byte string, decode it as ASCII, replacing
    # non-ASCII characters, so that we have at least something to log.
    if isinstance(file_name, bytes):
        file_name = file_name.decode("ascii", "replace")
    d = deferLater(
        clock, 0, send_event_node_mac_address,
        event_type=EVENT_TYPES.NODE_TFTP_REQUEST,
        mac_address=mac_address, description=file_name)
    d.addErrback(log.err, "Logging TFTP request failed.")


class TFTPBackend(FilesystemSynchronousBackend):
    """A partially dynamic read-only TFTP server.

    Static files such as kernels and initrds, as well as any non-MAAS files
    that the system may already be set up to serve, are served up normally.
    But PXE configurations are generated on the fly.

    When a PXE configuration file is requested, the server asynchronously
    requests the appropriate parameters from the API (at a configurable
    "generator URL") and generates a config file based on those.

    The regular expressions `re_config_file` and `re_mac_address` specify
    which files the server generates on the fly.  Any other requests are
    passed on to the filesystem.

    Passing requests on to the API must be done very selectively, because
    failures cause the boot process to halt. This is why the expression for
    matching the MAC address is so narrowly defined: PXELINUX attempts to
    fetch files at many similar paths which must not be passed on.
    """

    def __init__(self, base_path, generator_url, cluster_uuid):
        """
        :param base_path: The root directory for this TFTP server.
        :param generator_url: The URL which can be queried for the PXE
            config. See `get_generator_url` for the types of queries it is
            expected to accept.
        :param cluster_uuid: The cluster's UUID, as a string.
        """
        if not isinstance(base_path, FilePath):
            base_path = FilePath(base_path)
        super(TFTPBackend, self).__init__(
            base_path, can_read=True, can_write=False)
        self.generator_url = urlparse(generator_url)
        self.cluster_uuid = cluster_uuid
        self.fetcher = PageFetcher(agent=self.__class__)
        self.get_page = self.fetcher.get

    def get_generator_url(self, params):
        """Calculate the URL, including query, from which we can fetch
        additional configuration parameters.

        :param params: A dict, or iterable suitable for updating a dict, of
            additional query parameters.
        """
        query = {}
        # Merge parameters from the generator URL.
        query.update(parse_qsl(self.generator_url.query))
        # Merge parameters obtained from the request.
        query.update(params)
        # Merge updated query into the generator URL.
        url = self.generator_url._replace(query=urlencode(query))
        return ascii_url(url.geturl())

    @inlineCallbacks
    @typed
    def get_boot_method(self, file_name: TFTPPath):
        """Finds the correct boot method."""
        for _, method in BootMethodRegistry:
            params = yield maybeDeferred(method.match_path, self, file_name)
            if params is not None:
                params["bios_boot_method"] = method.bios_boot_method
                returnValue((method, params))
        returnValue((None, None))

    @deferred
    def get_kernel_params(self, params):
        """Return kernel parameters obtained from the API.

        :param params: Parameters so far obtained, typically from the file
            path requested.
        :return: A `KernelParameters` instance.
        """
        url = self.get_generator_url(params)

        def reassemble(data):
            return KernelParameters(**data)

        d = self.get_page(url)
        d.addCallback(lambda data: data.decode("ascii"))
        d.addCallback(json.loads)
        d.addCallback(reassemble)
        return d

    @deferred
    def get_boot_method_reader(self, boot_method, params):
        """Return an `IReader` for a boot method.

        :param boot_method: Boot method that is generating the config
        :param params: Parameters so far obtained, typically from the file
            path requested.
        """
        def generate(kernel_params):
            return boot_method.get_reader(
                self, kernel_params=kernel_params, **params)

        d = self.get_kernel_params(params)
        d.addCallback(generate)
        return d

    @staticmethod
    def get_page_errback(failure, file_name):
        failure.trap(twisted.web.error.Error)
        # This twisted.web.error.Error.status object ends up being a
        # string for some reason, but the constants we can compare against
        # (both in httplib and twisted.web.http) are ints.
        try:
            status_int = int(failure.value.status)
        except ValueError:
            # Assume that it's some other error and propagate it
            return failure

        if status_int == http.client.NO_CONTENT:
            # Convert HTTP No Content to a TFTP file not found
            raise FileNotFound(file_name)
        elif status_int == http.client.NOT_FOUND:
            # Convert HTTP NotFound to a TFTP file not found
            raise FileNotFound(file_name)
        else:
            # Otherwise propogate the unknown error
            return failure

    @deferred
    @typed
    def handle_boot_method(self, file_name: TFTPPath, result):
        boot_method, params = result
        if boot_method is None:
            return super(TFTPBackend, self).get_reader(file_name)

        # Map pxe namespace architecture names to MAAS's.
        arch = params.get("arch")
        if arch is not None:
            maasarch = ArchitectureRegistry.get_by_pxealias(arch)
            if maasarch is not None:
                params["arch"] = maasarch.name.split("/")[0]

        # Send the local and remote endpoint addresses.
        local_host, local_port = tftp.get_local_address()
        params["local"] = local_host
        remote_host, remote_port = tftp.get_remote_address()
        params["remote"] = remote_host
        params["cluster_uuid"] = self.cluster_uuid
        d = self.get_boot_method_reader(boot_method, params)
        return d

    @staticmethod
    def all_is_lost_errback(failure):
        if failure.check(BackendError):
            # This failure is something that the TFTP server knows how to deal
            # with, so pass it through.
            return failure
        else:
            # Something broke badly; record it.
            log.err(failure, "Starting TFTP back-end failed.")
            # Don't keep people waiting; tell them something broke right now.
            raise BackendError(failure.getErrorMessage())

    @deferred
    @typed
    def get_reader(self, file_name: TFTPPath):
        """See `IBackend.get_reader()`.

        If `file_name` matches a boot method then the response is obtained
        from that boot method. Otherwise the filesystem is used to service
        the response.
        """
        # It is possible for a client to request the file with '\' instead
        # of '/', example being 'bootx64.efi'. Convert all '\' to '/' to be
        # unix compatiable.
        file_name = file_name.replace(b'\\', b'/')
        mac_address = get_remote_mac()
        if mac_address is not None:
            log_request(mac_address, file_name)
        d = self.get_boot_method(file_name)
        d.addCallback(partial(self.handle_boot_method, file_name))
        d.addErrback(self.get_page_errback, file_name)
        d.addErrback(self.all_is_lost_errback)
        return d


class Port(udp.Port):
    """A :py:class:`udp.Port` that groks IPv6."""

    # This must be set by call sites.
    addressFamily = None

    def getHost(self):
        """See :py:meth:`twisted.internet.udp.Port.getHost`."""
        host, port = self.socket.getsockname()[:2]
        addr_type = IPv6Address if isIPv6Address(host) else IPv4Address
        return addr_type('UDP', host, port)


class UDPServer(internet.UDPServer):
    """A :py:class:`~internet.UDPServer` that groks IPv6.

    This creates the port directly instead of using the reactor's
    ``listenUDP`` method so that we can do a switcharoo to our own
    IPv6-enabled port implementation.
    """

    def _getPort(self):
        """See :py:meth:`twisted.application.internet.UDPServer._getPort`."""
        return self._listenUDP(*self.args, **self.kwargs)

    def _listenUDP(self, port, protocol, interface='', maxPacketSize=8192):
        """See :py:meth:`twisted.internet.reactor.listenUDP`."""
        p = Port(port, protocol, interface, maxPacketSize)
        p.addressFamily = AF_INET6 if isIPv6Address(interface) else AF_INET
        p.startListening()
        return p


class TFTPService(MultiService, object):
    """An umbrella service representing a set of running TFTP servers.

    Creates a UDP server individually for each discovered network
    interface, so that we can detect the interface via which we have
    received a datagram.

    It then periodically updates the servers running in case there's a
    change to the host machine's network configuration.

    :ivar backend: The :class:`TFTPBackend` being used to service TFTP
        requests.

    :ivar port: The port on which each server is started.

    :ivar refresher: A :class:`TimerService` that calls
        ``updateServers`` periodically.

    """

    def __init__(self, resource_root, port, generator, uuid):
        """
        :param resource_root: The root directory for this TFTP server.
        :param port: The port on which each server should be started.
        :param generator: The URL to be queried for PXE configuration.
            This will normally point to the `pxeconfig` endpoint on the
            region-controller API.
        :param uuid: The cluster's UUID, as a string.
        """
        super(TFTPService, self).__init__()
        self.backend = TFTPBackend(resource_root, generator, uuid)
        self.port = port
        # Establish a periodic call to self.updateServers() every 45
        # seconds, so that this service eventually converges on truth.
        # TimerService ensures that a call is made to it's target
        # function immediately as it's started, so there's no need to
        # call updateServers() from here.
        self.refresher = internet.TimerService(45, self.updateServers)
        self.refresher.setName("refresher")
        self.refresher.setServiceParent(self)

    def getServers(self):
        """Return a set of all configured servers.

        :rtype: :class:`set` of :class:`internet.UDPServer`
        """
        return {
            service for service in self
            if service is not self.refresher
        }

    def updateServers(self):
        """Run a server on every interface.

        For each configured network interface this will start a TFTP
        server. If called later it will bring up servers on newly
        configured interfaces and bring down servers on deconfigured
        interfaces.
        """
        addrs_established = set(service.name for service in self.getServers())
        addrs_desired = set(get_all_interface_addresses())

        for address in addrs_desired - addrs_established:
            if not IPAddress(address).is_link_local():
                tftp_service = UDPServer(
                    self.port, TFTP(self.backend), interface=address)
                tftp_service.setName(address)
                tftp_service.setServiceParent(self)

        for address in addrs_established - addrs_desired:
            tftp_service = self.getServiceNamed(address)
            tftp_service.disownServiceParent()
