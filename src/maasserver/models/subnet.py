# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Model for subnets."""

__all__ = [
    'create_cidr',
    'Subnet',
]

from operator import attrgetter
from typing import (
    Iterable,
    Optional,
)

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.core.validators import RegexValidator
from django.db.models import (
    BooleanField,
    CharField,
    ForeignKey,
    IntegerField,
    Manager,
    PROTECT,
    Q,
    TextField,
)
from django.db.models.query import QuerySet
from maasserver.enum import (
    IPADDRESS_TYPE,
    IPRANGE_TYPE,
    RDNS_MODE,
    RDNS_MODE_CHOICES,
)
from maasserver.exceptions import (
    MAASAPIException,
    StaticIPAddressExhaustion,
    StaticIPAddressOutOfRange,
    StaticIPAddressUnavailable,
)
from maasserver.fields import (
    CIDRField,
    MAASIPAddressField,
)
from maasserver.models.cleansave import CleanSave
from maasserver.models.staticroute import StaticRoute
from maasserver.models.timestampedmodel import TimestampedModel
from maasserver.utils.orm import MAASQueriesMixin
from netaddr import (
    AddrFormatError,
    IPAddress,
    IPNetwork,
)
from provisioningserver.logger import get_maas_logger
from provisioningserver.utils.network import (
    IPRANGE_TYPE as MAASIPRANGE_TYPE,
    MAASIPSet,
    make_ipaddress,
    make_iprange,
    MaybeIPAddress,
    parse_integer,
)


maaslog = get_maas_logger("subnet")

# Note: since subnets can be referenced in the API by name, if this regex is
# updated, then the regex in urls_api.py also needs to be udpated.
SUBNET_NAME_VALIDATOR = RegexValidator('^[.: \w/-]+$')

# Typing for list of IP addresses to exclude.
IPAddressExcludeList = Optional[Iterable[MaybeIPAddress]]


def get_default_vlan():
    from maasserver.models.vlan import VLAN
    return VLAN.objects.get_default_vlan().id


def create_cidr(network, subnet_mask=None):
    """Given the specified network and subnet mask, create a CIDR string.

    Discards any extra bits present in the 'network'. (bits which overlap
    zeroes in the netmask)

    Returns the object in unicode format, so that this function can be used
    in database migrations (which do not support custom fields).

    :param network:The network
    :param subnet_mask:An IPv4 or IPv6 netmask or prefix length
    :return:An IPNetwork representing the CIDR.
    """
    if isinstance(network, IPNetwork) and subnet_mask is None:
        return str(network.cidr)
    else:
        network = make_ipaddress(network)
    if subnet_mask is None and isinstance(network, (bytes, str)):
        if '/' in network:
            return str(IPNetwork(network).cidr)
        else:
            assert False, "Network passed as CIDR string must contain '/'."
    network = str(make_ipaddress(network))
    if isinstance(subnet_mask, int):
        mask = str(subnet_mask)
    else:
        mask = str(make_ipaddress(subnet_mask))
    cidr = IPNetwork(network + '/' + mask).cidr
    return str(cidr)


class SubnetQueriesMixin(MAASQueriesMixin):

    find_subnets_with_ip_query = """
        SELECT DISTINCT subnet.*, masklen(subnet.cidr) "prefixlen"
        FROM
            maasserver_subnet AS subnet
        WHERE
            %s << subnet.cidr
        ORDER BY prefixlen DESC
        """

    def raw_subnets_containing_ip(self, ip):
        """Find the most specific Subnet the specified IP address belongs in.
        """
        return self.raw(
            self.find_subnets_with_ip_query, params=[str(ip)])

    # Note: << is the postgresql "is contained within" operator.
    # See http://www.postgresql.org/docs/8.4/static/functions-net.html
    # Use an ORDER BY and LIMIT clause to match the most specific
    # subnet for the given IP address.
    # Also, when using "SELECT DISTINCT", the items in ORDER BY must be
    # present in the SELECT. (hence the extra field)
    find_best_subnet_for_ip_query = """
        SELECT DISTINCT
            subnet.*,
            masklen(subnet.cidr) "prefixlen",
            vlan.dhcp_on "dhcp_on"
        FROM maasserver_subnet AS subnet
        INNER JOIN maasserver_vlan AS vlan
            ON subnet.vlan_id = vlan.id
        WHERE
            %s << subnet.cidr /* Specified IP is inside range */
        ORDER BY
            /* Pick subnet that is on a VLAN that is managed over a subnet
               that is not managed on a VLAN. */
            dhcp_on DESC,
            /* If there are multiple subnets we want to pick the most specific
               one that the IP address falls within. */
            prefixlen DESC
        LIMIT 1
        """

    def get_best_subnet_for_ip(self, ip):
        """Find the most-specific managed Subnet the specified IP address
        belongs to."""
        subnets = self.raw(
            self.find_best_subnet_for_ip_query,
            params=[str(ip)])

        for subnet in subnets:
            return subnet  # This is stable because the query is ordered.
        else:
            return None

    def validate_filter_specifiers(self, specifiers):
        """Validate the given filter string."""
        try:
            self.filter_by_specifiers(specifiers)
        except (ValueError, AddrFormatError) as e:
            raise ValidationError(e.message)

    def get_specifiers_q(self, specifiers, separator=':', **kwargs):
        """Returns a Q object for objects matching the given specifiers.

        Allows a number of types to be prefixed in front of each specifier:
            * 'ip:' Matches the subnet that best matches the given IP address.
            * 'cidr:' Matches a subnet with the exact given CIDR.
            * 'name': Matches a subnet with the given name.
            * 'vid:' Matches a subnet whose VLAN has the given VID.
                Can be used with a hexadecimal or binary string by prefixing
                it with '0x' or '0b'.
            ' 'vlan:' Synonym for 'vid' for compatibility with older MAAS
                versions.
            * 'space:' Matches the name of this subnet's VLAN's space.

        If no specifier is given, the input will be treated as a CIDR. If
        the input is not a valid CIDR, it will be treated as subnet name.

        :raise:AddrFormatError:If a specific IP address or CIDR is requested,
            but the address could not be parsed.

        :return:django.db.models.Q
        """
        # Circular imports.
        from maasserver.models import (
            Fabric,
            Interface,
            Space,
            VLAN,
        )

        # This dict is used by the constraints code to identify objects
        # with particular properties. Please note that changing the keys here
        # can impact backward compatibility, so use caution.
        specifier_types = {
            None: self._add_default_query,
            'cidr': self._add_unvalidated_cidr_query,
            'fabric': (Fabric.objects, 'vlan__subnet'),
            'id': self._add_subnet_id_query,
            'interface': (Interface.objects, 'ip_addresses__subnet'),
            'ip': self._add_ip_in_subnet_query,
            'name': "__name",
            'space': (Space.objects, 'vlan__subnet'),
            'vid': self._add_vlan_vid_query,
            'vlan': (VLAN.objects, 'subnet'),
        }
        return super(SubnetQueriesMixin, self).get_specifiers_q(
            specifiers, specifier_types=specifier_types, separator=separator,
            **kwargs)

    def _add_default_query(self, current_q, op, item):
        """If the item we're matching is an integer, first try to locate the
        subnet by its ID. Otherwise, try to parse it as a CIDR. If all else
        fails, search by the name.
        """
        id = self.get_object_id(item)
        if id is not None:
            return op(current_q, Q(id=id))

        try:
            ip = IPNetwork(item)
        except (AddrFormatError, ValueError):
            # The user didn't pass in a valid CIDR, so try the subnet name.
            return op(current_q, Q(name=item))
        else:
            cidr = str(ip.cidr)
            return op(current_q, Q(cidr=cidr))

    def _add_unvalidated_cidr_query(self, current_q, op, item):
        ip = IPNetwork(item)
        cidr = str(ip.cidr)
        current_q = op(current_q, Q(cidr=cidr))
        return current_q

    def _add_ip_in_subnet_query(self, current_q, op, item):
        # Try to validate this before it hits the database, since this
        # is going to be a raw query.
        item = str(IPAddress(item))
        # This is a special case. If a specific IP filter is included,
        # a custom query is needed to get the result. We can't chain
        # a raw query using Q without grabbing the IDs first.
        ids = self.get_id_list(self.raw_subnets_containing_ip(item))
        current_q = op(current_q, Q(id__in=ids))
        return current_q

    def _add_subnet_id_query(self, current_q, op, item):
        try:
            item = parse_integer(item)
        except ValueError:
            raise ValidationError("Subnet ID must be numeric.")
        else:
            current_q = op(current_q, Q(id=item))
            return current_q


class SubnetQuerySet(QuerySet, SubnetQueriesMixin):
    """Custom QuerySet which mixes in some additional queries specific to
    subnets. This needs to be a mixin because an identical method is needed on
    both the Manager and all QuerySets which result from calling the manager.
    """


class SubnetManager(Manager, SubnetQueriesMixin):
    """Manager for :class:`Subnet` model."""

    def get_queryset(self):
        queryset = SubnetQuerySet(self.model, using=self._db)
        return queryset

    def create_from_cidr(self, cidr, vlan=None):
        """Create a subnet from the given CIDR."""
        name = "subnet-" + str(cidr)
        from maasserver.models import VLAN
        if vlan is None:
            vlan = VLAN.objects.get_default_vlan()
        return self.create(name=name, cidr=cidr, vlan=vlan)

    def _find_fabric(self, fabric):
        from maasserver.models import Fabric

        if fabric is None:
            # If no Fabric is specified, use the default. (will always be 0)
            fabric = 0
        elif isinstance(fabric, Fabric):
            fabric = fabric.id
        else:
            fabric = int(fabric)
        return fabric

    def get_cidr_list_for_periodic_active_scan(self):
        """Returns the list of subnets which allow a periodic active scan.

        :return: list of `netaddr.IPNetwork` objects.
        """
        query = self.filter(active_discovery=True)
        return [
            IPNetwork(cidr)
            for cidr in query.values_list('cidr', flat=True)
        ]

    def get_subnet_or_404(self, specifiers, user, perm):
        """Fetch a `Subnet` by its id.  Raise exceptions if no `Subnet` with
        this id exists or if the provided user has not the required permission
        to access this `Subnet`.

        :param specifiers: A specifier to uniquely locate the Subnet.
        :type specifiers: unicode
        :param user: The user that should be used in the permission check.
        :type user: django.contrib.auth.models.User
        :param perm: The permission to assert that the user has on the node.
        :type perm: unicode
        :raises: django.http.Http404_,
            :class:`maasserver.exceptions.PermissionDenied`.

        .. _django.http.Http404: https://
           docs.djangoproject.com/en/dev/topics/http/views/
           #the-http404-exception
        """
        subnet = self.get_object_by_specifiers_or_raise(specifiers)
        if user.has_perm(perm, subnet):
            return subnet
        else:
            raise PermissionDenied()


class Subnet(CleanSave, TimestampedModel):

    def __init__(self, *args, **kwargs):
        assert 'space' not in kwargs, "Subnets can no longer be in spaces."
        super().__init__(*args, **kwargs)

    objects = SubnetManager()

    name = CharField(
        blank=False, editable=True, max_length=255,
        validators=[SUBNET_NAME_VALIDATOR],
        help_text="Identifying name for this subnet.")

    description = TextField(null=False, blank=True)

    vlan = ForeignKey(
        'VLAN', default=get_default_vlan, editable=True, blank=False,
        null=False, on_delete=PROTECT)

    # XXX:fabric: unique constraint should be relaxed once proper support for
    # fabrics is implemented. The CIDR must be unique withing a Fabric, not
    # globally unique.
    cidr = CIDRField(
        blank=False, unique=True, editable=True, null=False)

    rdns_mode = IntegerField(
        choices=RDNS_MODE_CHOICES, editable=True,
        default=RDNS_MODE.DEFAULT)

    gateway_ip = MAASIPAddressField(blank=True, editable=True, null=True)

    dns_servers = ArrayField(
        TextField(), blank=True, editable=True, null=True, default=list)

    allow_proxy = BooleanField(
        editable=True, blank=False, null=False, default=True)

    active_discovery = BooleanField(
        editable=True, blank=False, null=False, default=False)

    managed = BooleanField(
        editable=True, blank=False, null=False, default=True)

    @property
    def label(self):
        """Returns a human-friendly label for this subnet."""
        cidr = str(self.cidr)
        # Note: there is a not-NULL check for the 'name' field, so this only
        # applies to unsaved objects.
        if self.name is None or self.name == "":
            return cidr
        if cidr not in self.name:
            return "%s (%s)" % (self.name, self.cidr)
        else:
            return self.name

    @property
    def space(self):
        """Backward compatibility shim to get the space for this subnet."""
        return self.vlan.space

    def get_ipnetwork(self) -> IPNetwork:
        return IPNetwork(self.cidr)

    def get_ip_version(self) -> int:
        return self.get_ipnetwork().version

    def update_cidr(self, cidr):
        cidr = str(cidr)
        # If the old name had the CIDR embedded in it, update that first.
        if self.name:
            self.name = self.name.replace(str(self.cidr), cidr)
        else:
            self.name = cidr
        self.cidr = cidr

    def __str__(self):
        return "%s:%s(vid=%s)" % (
            self.name, self.cidr, self.vlan.vid)

    def validate_gateway_ip(self):
        if self.gateway_ip is None or self.gateway_ip == '':
            return
        gateway_addr = IPAddress(self.gateway_ip)
        network = self.get_ipnetwork()
        if gateway_addr in network:
            # If the gateway is in the network, it is fine.
            return
        elif network.version == 6 and gateway_addr.is_link_local():
            # If this is an IPv6 network and the gateway is in the link-local
            # network (fe80::/64 -- required to be configured by the spec),
            # then it is also valid.
            return
        else:
            # The gateway is not valid for the network.
            message = "Gateway IP must be within CIDR range."
            raise ValidationError({'gateway_ip': [message]})

    def clean_fields(self, *args, **kwargs):
        # XXX mpontillo 2016-03-16: this function exists due to bug #1557767.
        # This workaround exists to prevent potential unintended consequences
        # of making the name optional.
        if (self.name is None or self.name == '') and self.cidr is not None:
            self.name = str(self.cidr)
        super().clean_fields(*args, **kwargs)

    def clean(self, *args, **kwargs):
        self.validate_gateway_ip()

    def delete(self, *args, **kwargs):
        # Check if DHCP is enabled on the VLAN this subnet is attached to.
        if self.vlan.dhcp_on and self.get_dynamic_ranges().exists():
            raise ValidationError(
                "Cannot delete a subnet that is actively servicing a dynamic "
                "IP range. (Delete the dynamic range or disable DHCP first.)")
        super().delete(*args, **kwargs)

    def _get_ranges_for_allocated_ips(
            self, ipnetwork: IPNetwork, ignore_discovered_ips: bool) -> set:
        """Returns a set of MAASIPRange objects created from the set of allocated
        StaticIPAddress objects.
        """
        # Note, the original implementation used .exclude() to filter,
        # but we'll filter at runtime so that prefetch_related in the
        # websocket works properly.
        ranges = set()
        for sip in self.staticipaddress_set.all():
            if sip.ip and not (ignore_discovered_ips and (
                    sip.alloc_type == IPADDRESS_TYPE.DISCOVERED)):
                ip = IPAddress(sip.ip)
                if ip in ipnetwork:
                    ranges.add(make_iprange(ip, purpose="assigned-ip"))
        return ranges

    def get_ipranges_in_use(
            self, exclude_addresses: IPAddressExcludeList=None,
            ranges_only: bool=False, include_reserved: bool=True,
            with_neighbours: bool=False,
            ignore_discovered_ips: bool=False) -> MAASIPSet:
        """Returns a `MAASIPSet` of `MAASIPRange` objects which are currently
        in use on this `Subnet`.

        :param exclude_addresses: Additional addresses to consider "in use".
        :param ignore_discovered_ips: DISCOVERED addresses are not "in use".
        :param ranges_only: if True, filters out gateway IPs, static routes,
            DNS servers, and `exclude_addresses`.
        :param with_neighbours: If True, includes addresses learned from
            neighbour observation.
        """
        if exclude_addresses is None:
            exclude_addresses = []
        ranges = set()
        network = self.get_ipnetwork()
        if network.version == 6:
            # For most IPv6 networks, automatically reserve the range:
            #     ::1 - ::ffff:ffff
            # We expect the administrator will be using ::1 through ::ffff.
            # We plan to reserve ::1:0 through ::ffff:ffff for use by MAAS,
            # so that we can allocate addresses in the form:
            #     ::<node>:<child>
            # For now, just make sure IPv6 addresses are allocated from
            # *outside* both ranges, so that they won't conflict with addresses
            # reserved from this scheme in the future.
            first = str(IPAddress(network.first))
            first_plus_one = str(IPAddress(network.first + 1))
            second = str(IPAddress(network.first + 0xFFFFFFFF))
            if network.prefixlen == 64:
                ranges |= {make_iprange(
                    first_plus_one, second, purpose="reserved")}
            # Reserve the subnet router anycast address, except for /127 and
            # /128 networks. (See RFC 6164, and RFC 4291 section 2.6.1.)
            if network.prefixlen < 127:
                ranges |= {make_iprange(
                    first, first, purpose="rfc-4291-2.6.1")}
        ipnetwork = self.get_ipnetwork()
        if not ranges_only:
            if (self.gateway_ip is not None and self.gateway_ip != '' and
                    self.gateway_ip in ipnetwork):
                ranges |= {make_iprange(self.gateway_ip, purpose="gateway-ip")}
            if self.dns_servers is not None:
                ranges |= set(
                    make_iprange(server, purpose="dns-server")
                    for server in self.dns_servers
                    if server in ipnetwork
                )
            for static_route in StaticRoute.objects.filter(source=self):
                ranges |= {
                    make_iprange(
                        static_route.gateway_ip, purpose="gateway-ip")}
            ranges |= self._get_ranges_for_allocated_ips(
                ipnetwork, ignore_discovered_ips)
            ranges |= set(
                make_iprange(address, purpose="excluded")
                for address in exclude_addresses
                if address in network
            )
        if include_reserved:
            ranges |= self.get_reserved_maasipset()
        ranges |= self.get_dynamic_maasipset()
        if with_neighbours:
            ranges |= self.get_maasipset_for_neighbours()
        return MAASIPSet(ranges)

    def get_ipranges_available_for_reserved_range(self):
        return self.get_ipranges_not_in_use(ranges_only=True)

    def get_ipranges_available_for_dynamic_range(self):
        return self.get_ipranges_not_in_use(
            ranges_only=False, ignore_discovered_ips=True)

    def get_ipranges_not_in_use(
            self, exclude_addresses: IPAddressExcludeList=None,
            ranges_only: bool=False, ignore_discovered_ips: bool=False,
            with_neighbours: bool=False) -> MAASIPSet:
        """Returns a `MAASIPSet` of ranges which are currently free on this
        `Subnet`.

        :param ranges_only: if True, filters out gateway IPs, static routes,
            DNS servers, and `exclude_addresses`.
        :param exclude_addresses: An iterable of addresses not to use.
        :param ignore_discovered_ips: DISCOVERED addresses are not "in use".
        :param with_neighbours: If True, includes addresses learned from
            neighbour observation.
        """
        if exclude_addresses is None:
            exclude_addresses = []
        in_use = self.get_ipranges_in_use(
            exclude_addresses=exclude_addresses,
            ranges_only=ranges_only,
            with_neighbours=with_neighbours,
            ignore_discovered_ips=ignore_discovered_ips)
        if self.managed or ranges_only:
            not_in_use = in_use.get_unused_ranges(self.get_ipnetwork())
        else:
            # The end result we want is a list of unused IP addresses *within*
            # reserved ranges. To get that result, we first need the full list
            # of unused IP addresses on the subnet. This is better illustrated
            # visually below.
            #
            # Legend:
            #     X:  in-use IP addresses
            #     R:  reserved range
            #     Rx: reserved range (with allocated, in-use IP address)
            #
            #             +----+----+----+----+----+----+
            # IP address: | 1  | 2  | 3  | 4  | 5  | 6  |
            #             +----+----+----+----+----+----+
            #     Usages: | X  |    | R  | Rx |    | X  |
            #             +----+----+----+----+----+----+
            #
            # We need a set that just contains `3` in this case. To get there,
            # first calculate the set of all unused addresses on the subnet,
            # then intersect that set with set of in-use addresses *excluding*
            # the reserved range, then calculate which addresses within *that*
            # set are unused:
            #                               +----+----+----+----+----+----+
            #                   IP address: | 1  | 2  | 3  | 4  | 5  | 6  |
            #                               +----+----+----+----+----+----+
            #                       unused: |    | U  |    |    | U  |    |
            #                               +----+----+----+----+----+----+
            #             unmanaged_in_use: | u  |    |    | u  |    | u  |
            #                               +----+----+----+----+----+----+
            #                 |= unmanaged: ===============================
            #                               +----+----+----+----+----+----+
            #             unmanaged_in_use: | u  | U  |    | u  | U  | u  |
            #                               +----+----+----+----+----+----+
            #          get_unused_ranges(): ===============================
            #                               +----+----+----+----+----+----+
            #                   not_in_use: |    |    | n  |    |    |    |
            #                               +----+----+----+----+----+----+
            unused = in_use.get_unused_ranges(
                self.get_ipnetwork(), purpose=MAASIPRANGE_TYPE.UNMANAGED)
            unmanaged_in_use = self.get_ipranges_in_use(
                exclude_addresses=exclude_addresses,
                ranges_only=ranges_only,
                include_reserved=False,
                with_neighbours=with_neighbours,
                ignore_discovered_ips=ignore_discovered_ips)
            unmanaged_in_use |= unused
            not_in_use = unmanaged_in_use.get_unused_ranges(
                self.get_ipnetwork(), purpose=MAASIPRANGE_TYPE.UNUSED)
        return not_in_use

    def get_maasipset_for_neighbours(self) -> MAASIPSet:
        """Return the observed neighbours in this subnet.

        :return: MAASIPSet of neighbours (with the "neighbour" purpose).
        """
        # Circular imports.
        from maasserver.models import Discovery
        # Note: we only need unknown IP addresses here, because the known
        # IP addresses should already be covered by get_ipranges_in_use().
        neighbours = Discovery.objects.filter(subnet=self).by_unknown_ip()
        neighbour_set = {
            make_iprange(neighbour.ip, purpose="neighbour")
            for neighbour in neighbours
        }
        return MAASIPSet(neighbour_set)

    def get_least_recently_seen_unknown_neighbour(self):
        """
        Returns the least recently seen unknown neighbour or this subnet.

        Useful when allocating an IP address, to safeguard against assigning
        an address another host is still using.

        :return: a `maasserver.models.Discovery` object
        """
        # Circular imports.
        from maasserver.models import Discovery
        return Discovery.objects.filter(
            subnet=self).by_unknown_ip().order_by('last_seen').first()

    def get_iprange_usage(self, with_neighbours=False) -> MAASIPSet:
        """Returns both the reserved and unreserved IP ranges in this Subnet.
        (This prevents a potential race condition that could occur if an IP
        address is allocated or deallocated between calls.)

        :returns: A tuple indicating the (reserved, unreserved) ranges.
        """
        reserved_ranges = self.get_ipranges_in_use()
        if with_neighbours is True:
            reserved_ranges |= self.get_maasipset_for_neighbours()
        return reserved_ranges.get_full_range(self.get_ipnetwork())

    def get_next_ip_for_allocation(
            self, exclude_addresses: Optional[Iterable]=None,
            avoid_observed_neighbours: bool=True):
        """Heuristic to return the "best" address from this subnet to use next.

        :param exclude_addresses: Optional list of addresses to exclude.
        :param avoid_observed_neighbours: Optional parameter to specify if
            known observed neighbours should be avoided. This parameter is not
            intended to be specified by a caller in production code; it is used
            internally to recursively call this method if the first allocation
            attempt fails.
        """
        if exclude_addresses is None:
            exclude_addresses = []
        free_ranges = self.get_ipranges_not_in_use(
            exclude_addresses=exclude_addresses,
            with_neighbours=avoid_observed_neighbours)
        if len(free_ranges) == 0 and avoid_observed_neighbours is True:
            # Try again recursively, but this time consider neighbours to be
            # "free" IP addresses. (We'll pick the least recently seen IP.)
            return self.get_next_ip_for_allocation(
                exclude_addresses, avoid_observed_neighbours=False)
        elif len(free_ranges) == 0:
            raise StaticIPAddressExhaustion(
                "No more IPs available in subnet: %s." % self.cidr)
        # The first time through this function, we aren't trying to avoid
        # observed neighbours. In fact, `free_ranges` only contains completely
        # unused ranges. So we don't need to check for the least recently seen
        # neighbour on the first pass.
        if avoid_observed_neighbours is False:
            # We tried considering neighbours as "in-use" addresses, but the
            # subnet is still full. So make an educated guess about which IP
            # address is least likely to be in-use.
            discovery = self.get_least_recently_seen_unknown_neighbour()
            if discovery is not None:
                maaslog.warning(
                    "Next IP address to allocate from '%s' has been observed "
                    "previously: %s was last claimed by %s via %s at %s." % (
                        self.label, discovery.ip, discovery.mac_address,
                        discovery.observer_interface.get_log_string(),
                        discovery.last_seen))
                return str(discovery.ip)
        # The purpose of this is to that we ensure we always get an IP address
        # from the *smallest* free contiguous range. This way, larger ranges
        # can be preserved in case they need to be used for applications
        # requiring them.
        free_range = min(free_ranges, key=attrgetter('num_addresses'))
        return str(IPAddress(free_range.first))

    def render_json_for_related_ips(
            self, with_username=True, with_node_summary=True):
        """Render a representation of this subnet's related IP addresses,
        suitable for converting to JSON. Optionally exclude user and node
        information."""
        ip_addresses = self.staticipaddress_set.all()
        return sorted([
            ip.render_json(
                with_username=with_username,
                with_node_summary=with_node_summary)
            for ip in ip_addresses
            if ip.ip
            ], key=lambda json: IPAddress(json['ip']))

    def get_dynamic_ranges(self):
        return self.iprange_set.filter(type=IPRANGE_TYPE.DYNAMIC)

    def get_reserved_ranges(self):
        return self.iprange_set.filter(type=IPRANGE_TYPE.RESERVED)

    def is_valid_static_ip(self, *args, **kwargs):
        """Validates that the requested IP address is acceptable for allocation
        in this `Subnet` (assuming it has not already been allocated).

        Returns `True` if the IP address is acceptable, and `False` if not.

        Does not consider whether or not the IP address is already allocated,
        only whether or not it is in the proper network and range.

        :return: bool
        """
        try:
            self.validate_static_ip(*args, **kwargs)
        except MAASAPIException:
            return False
        return True

    def validate_static_ip(self, ip):
        """Validates that the requested IP address is acceptable for allocation
        in this `Subnet` (assuming it has not already been allocated).

        Raises `StaticIPAddressUnavailable` if the address is not acceptable.

        Does not consider whether or not the IP address is already allocated,
        only whether or not it is in the proper network and range.

        :raises StaticIPAddressUnavailable: If the IP address specified is not
            available for allocation.
        """
        if ip not in self.get_ipnetwork():
            raise StaticIPAddressOutOfRange(
                "%s is not within subnet CIDR: %s" % (ip, self.cidr))
        for iprange in self.get_reserved_maasipset():
            if ip in iprange:
                raise StaticIPAddressUnavailable(
                    "%s is within the reserved range from %s to %s" % (
                        ip, IPAddress(iprange.first), IPAddress(iprange.last)))
        for iprange in self.get_dynamic_maasipset():
            if ip in iprange:
                raise StaticIPAddressUnavailable(
                    "%s is within the dynamic range from %s to %s" % (
                        ip, IPAddress(iprange.first), IPAddress(iprange.last)))

    def get_reserved_maasipset(self):
        reserved_ranges = MAASIPSet(
            iprange.get_MAASIPRange()
            for iprange in self.get_reserved_ranges()
        )
        return reserved_ranges

    def get_dynamic_maasipset(self):
        dynamic_ranges = MAASIPSet(
            iprange.get_MAASIPRange()
            for iprange in self.get_dynamic_ranges()
        )
        return dynamic_ranges

    def get_dynamic_range_for_ip(self, ip):
        """Return `IPRange` for the provided `ip`."""
        # XXX mpontillo 2016-01-21: for some reason this query doesn't work.
        # I tried it both like this, and with:
        #     start_ip__gte=ip, and end_ip__lte=ip
        # return get_one(self.get_dynamic_ranges().extra(
        #        where=["start_ip >= inet '%s'" % ip,
        # ... which sounds a lot like comment 15 in:
        #     https://code.djangoproject.com/ticket/11442
        for iprange in self.get_dynamic_ranges():
            if ip in iprange.netaddr_iprange:
                return iprange
        return None

    def get_smallest_enclosing_sane_subnet(self):
        """Return the subnet that includes this subnet.

        It must also be at least big enough to be a parent in the RFC2317
        world (/24 in IPv4, /124 in IPv6).

        If no such subnet exists, return None.
        """
        find_rfc2137_parent_query = """
            SELECT * FROM maasserver_subnet
            WHERE
                %s << cidr AND (
                    (family(cidr) = 6 and masklen(cidr) <= 124) OR
                    (family(cidr) = 4 and masklen(cidr) <= 24))
            ORDER BY
                masklen(cidr) DESC
            LIMIT 1
            """
        for s in Subnet.objects.raw(find_rfc2137_parent_query, (self.cidr,)):
            return s
        return None
