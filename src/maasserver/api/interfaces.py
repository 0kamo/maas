# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""API handlers: `Interface`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type

from maasserver.api.support import (
    operation,
    OperationsHandler,
)
from maasserver.enum import (
    INTERFACE_TYPE,
    NODE_PERMISSION,
    NODE_STATUS,
)
from maasserver.exceptions import (
    MAASAPIValidationError,
    NodeStateViolation,
)
from maasserver.forms_interface import (
    BondInterfaceForm,
    InterfaceForm,
    PhysicalInterfaceForm,
    VLANInterfaceForm,
)
from maasserver.forms_interface_link import (
    InterfaceLinkForm,
    InterfaceSetDefaultGatwayForm,
    InterfaceUnlinkForm,
)
from maasserver.models.interface import (
    BondInterface,
    Interface,
    PhysicalInterface,
    VLANInterface,
)
from maasserver.models.node import Node
from piston.utils import rc


MISSING_FIELD = "This field is required."

BLANK_FIELD = "This field cannot be blank."

DISPLAYED_INTERFACE_FIELDS = (
    'id',
    'name',
    'type',
    'vlan',
    'mac_address',
    'parents',
    'children',
    'tags',
    'enabled',
    'links',
    'params',
    'discovered',
    'effective_mtu',
)


def raise_error_for_invalid_state_on_allocated_operations(
        node, user, operation):
    if node.status not in (NODE_STATUS.READY, NODE_STATUS.BROKEN):
        raise NodeStateViolation(
            "Cannot %s interface because the node is not Ready." % operation)


class NodeInterfacesHandler(OperationsHandler):
    """Manage interfaces on a node."""
    api_doc_section_name = "Node Interfaces"
    create = update = delete = None
    fields = DISPLAYED_INTERFACE_FIELDS

    @classmethod
    def resource_uri(cls, *args, **kwargs):
        # See the comment in NodeHandler.resource_uri.
        return ('node_interfaces_handler', ["system_id"])

    def read(self, request, system_id):
        """List all interfaces belonging to node.

        Returns 404 if the node is not found.
        """
        node = Node.nodes.get_node_or_404(
            system_id, request.user, NODE_PERMISSION.VIEW)
        return node.interface_set.all()

    @operation(idempotent=False)
    def create_physical(self, request, system_id):
        """Create a physical interface on node.

        :param name: Name of the interface.
        :param mac_address: MAC address of the interface.
        :param tags: Tags for the interface.
        :param vlan: Untagged VLAN the interface is connected to.

        Following are extra parameters that can be set on the interface:

        :param mtu: Maximum transmission unit.
        :param accept_ra: Accept router advertisements. (IPv6 only)
        :param autoconf: Perform stateless autoconfiguration. (IPv6 only)

        Returns 404 if the node is not found.
        """
        node = Node.nodes.get_node_or_404(
            system_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            node, request.user, "create")
        form = PhysicalInterfaceForm(node=node, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            # The Interface model validation is so strict that it will cause
            # the mac_address field to include two messages about it being
            # required. We clean up this response to not provide duplicate
            # information.
            if "mac_address" in form.errors:
                if (MISSING_FIELD in form.errors["mac_address"] and
                        BLANK_FIELD in form.errors["mac_address"]):
                    form.errors["mac_address"].remove(BLANK_FIELD)
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=False)
    def create_bond(self, request, system_id):
        """Create a bond interface on node.

        :param name: Name of the interface.
        :param mac_address: MAC address of the interface.
        :param tags: Tags for the interface.
        :param vlan: VLAN the interface is connected to.
        :param parents: Parent interfaces that make this bond.

        Following are parameters specific to bonds:

        :param bond_mode: The operating mode of the bond.
            (Default: active-backup).
        :param bond_miimon: The link monitoring freqeuncy in milliseconds.
            (Default: 100).
        :param bond_downdelay: Specifies the time, in milliseconds, to wait
            before disabling a slave after a link failure has been detected.
        :param bond_updelay: Specifies the time, in milliseconds, to wait
            before enabling a slave after a link recovery has been detected.
        :param bond_lacp_rate: Option specifying the rate in which we'll ask
            our link partner to transmit LACPDU packets in 802.3ad mode.
            Available options are fast or slow. (Default: slow).
        :param bond_xmit_hash_policy: The transmit hash policy to use for
            slave selection in balance-xor, 802.3ad, and tlb modes.
            (Default: layer2)

        Supported bonding modes (bond-mode):
        balance-rr - Transmit packets in sequential order from the first
        available slave through the last.  This mode provides load balancing
        and fault tolerance.

        active-backup - Only one slave in the bond is active.  A different
        slave becomes active if, and only if, the active slave fails.  The
        bond's MAC address is externally visible on only one port (network
        adapter) to avoid confusing the switch.

        balance-xor - Transmit based on the selected transmit hash policy.
        The default policy is a simple [(source MAC address XOR'd with
        destination MAC address XOR packet type ID) modulo slave count].

        broadcast - Transmits everything on all slave interfaces. This mode
        provides fault tolerance.

        802.3ad - IEEE 802.3ad Dynamic link aggregation.  Creates aggregation
        groups that share the same speed and duplex settings.  Utilizes all
        slaves in the active aggregator according to the 802.3ad specification.

        balance-tlb - Adaptive transmit load balancing: channel bonding that
        does not require any special switch support.

        balance-alb - Adaptive load balancing: includes balance-tlb plus
        receive load balancing (rlb) for IPV4 traffic, and does not require any
        special switch support.  The receive load balancing is achieved by
        ARP negotiation.

        Following are extra parameters that can be set on the interface:

        :param mtu: Maximum transmission unit.
        :param accept_ra: Accept router advertisements. (IPv6 only)
        :param autoconf: Perform stateless autoconfiguration. (IPv6 only)

        Returns 404 if the node is not found.
        """
        node = Node.nodes.get_node_or_404(
            system_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            node, request.user, "create bond")
        form = BondInterfaceForm(node=node, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=False)
    def create_vlan(self, request, system_id):
        """Create a VLAN interface on node.

        :param tags: Tags for the interface.
        :param vlan: Tagged VLAN the interface is connected to.
        :param parent: Parent interface for this VLAN interface.

        Following are extra parameters that can be set on the interface:

        :param mtu: Maximum transmission unit.
        :param accept_ra: Accept router advertisements. (IPv6 only)
        :param autoconf: Perform stateless autoconfiguration. (IPv6 only)

        Returns 404 if the node is not found.
        """
        node = Node.nodes.get_node_or_404(
            system_id, request.user, NODE_PERMISSION.ADMIN)
        # Cast parent to parents to make it easier on the user and to make it
        # work with the form.
        request.data = request.data.copy()
        if 'parent' in request.data:
            request.data['parents'] = request.data['parent']
        form = VLANInterfaceForm(node=node, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            # Replace parents with parent so it matches the API parameter.
            if 'parents' in form.errors:
                form.errors['parent'] = form.errors.pop('parents')
            raise MAASAPIValidationError(form.errors)


class NodeInterfaceHandler(OperationsHandler):
    """Manage a node's interface."""
    api_doc_section_name = "Node Interface"
    create = None
    model = Interface
    fields = DISPLAYED_INTERFACE_FIELDS

    @classmethod
    def resource_uri(cls, interface=None):
        # See the comment in NodeHandler.resource_uri.
        system_id = "system_id"
        interface_id = "interface_id"
        if interface is not None:
            interface_id = interface.id
            node = interface.get_node()
            if node is not None:
                system_id = node.system_id
        return ('node_interface_handler', (system_id, interface_id))

    @classmethod
    def mac_address(cls, interface):
        if interface.mac_address is not None:
            return "%s" % interface.mac_address
        else:
            return None

    @classmethod
    def parents(cls, interface):
        return sorted(
            nic.name
            for nic in interface.parents.all()
        )

    @classmethod
    def children(cls, interface):
        return sorted(
            nic.child.name
            for nic in interface.children_relationships.all()
        )

    @classmethod
    def links(cls, interface):
        return interface.get_links()

    @classmethod
    def discovered(cls, interface):
        return interface.get_discovered()

    @classmethod
    def effective_mtu(cls, interface):
        return interface.get_effective_mtu()

    def read(self, request, system_id, interface_id):
        """Read interface on node.

        Returns 404 if the node or interface is not found.
        """
        return Interface.objects.get_interface_or_404(
            system_id, interface_id, request.user, NODE_PERMISSION.VIEW)

    def update(self, request, system_id, interface_id):
        """Update interface on node.

        Fields for physical interface:
        :param name: Name of the interface.
        :param mac_address: MAC address of the interface.
        :param tags: Tags for the interface.
        :param vlan: Untagged VLAN the interface is connected to.

        Fields for bond interface:
        :param name: Name of the interface.
        :param mac_address: MAC address of the interface.
        :param tags: Tags for the interface.
        :param vlan: Tagged VLAN the interface is connected to.
        :param parents: Parent interfaces that make this bond.

        Fields for VLAN interface:
        :param tags: Tags for the interface.
        :param vlan: VLAN the interface is connected to.
        :param parent: Parent interface for this VLAN interface.

        Following are extra parameters that can be set on all interface types:

        :param mtu: Maximum transmission unit.
        :param accept_ra: Accept router advertisements. (IPv6 only)
        :param autoconf: Perform stateless autoconfiguration. (IPv6 only)

        Following are parameters specific to bonds:

        :param bond-mode: The operating mode of the bond.
            (Default: active-backup).
        :param bond-miimon: The link monitoring freqeuncy in milliseconds.
            (Default: 100).
        :param bond-downdelay: Specifies the time, in milliseconds, to wait
            before disabling a slave after a link failure has been detected.
        :param bond-updelay: Specifies the time, in milliseconds, to wait
            before enabling a slave after a link recovery has been detected.
        :param bond-lacp_rate: Option specifying the rate in which we'll ask
            our link partner to transmit LACPDU packets in 802.3ad mode.
            Available options are fast or slow. (Default: slow).
        :param bond-xmit_hash_policy: The transmit hash policy to use for
            slave selection in balance-xor, 802.3ad, and tlb modes.

        Supported bonding modes (bond-mode):
        balance-rr - Transmit packets in sequential order from the first
        available slave through the last.  This mode provides load balancing
        and fault tolerance.

        active-backup - Only one slave in the bond is active.  A different
        slave becomes active if, and only if, the active slave fails.  The
        bond's MAC address is externally visible on only one port (network
        adapter) to avoid confusing the switch.

        balance-xor - Transmit based on the selected transmit hash policy.
        The default policy is a simple [(source MAC address XOR'd with
        destination MAC address XOR packet type ID) modulo slave count].

        broadcast - Transmits everything on all slave interfaces. This mode
        provides fault tolerance.

        802.3ad - IEEE 802.3ad Dynamic link aggregation.  Creates aggregation
        groups that share the same speed and duplex settings.  Utilizes all
        slaves in the active aggregator according to the 802.3ad specification.

        balance-tlb - Adaptive transmit load balancing: channel bonding that
        does not require any special switch support.

        balance-alb - Adaptive load balancing: includes balance-tlb plus
        receive load balancing (rlb) for IPV4 traffic, and does not require any
        special switch support.  The receive load balancing is achieved by
        ARP negotiation.

        Returns 404 if the node or interface is not found.
        """
        interface = Interface.objects.get_interface_or_404(
            system_id, interface_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            interface.node, request.user, "update interface")
        interface_form = InterfaceForm.get_interface_form(interface.type)
        # For VLAN interface we cast parents to parent. As a VLAN can only
        # have one parent.
        if interface.type == INTERFACE_TYPE.VLAN:
            request.data = request.data.copy()
            if 'parent' in request.data:
                request.data['parents'] = request.data['parent']
        form = interface_form(instance=interface, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            # Replace parents with parent so it matches the API parameter, if
            # the interface being editted was a VLAN interface.
            if (interface.type == INTERFACE_TYPE.VLAN and
                    'parents' in form.errors):
                form.errors['parent'] = form.errors.pop('parents')
            raise MAASAPIValidationError(form.errors)

    def delete(self, request, system_id, interface_id):
        """Delete interface on node.

        Returns 404 if the node or interface is not found.
        """
        interface = Interface.objects.get_interface_or_404(
            system_id, interface_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            interface.node, request.user, "delete interface")
        interface.delete()
        return rc.DELETED

    @operation(idempotent=False)
    def link_subnet(self, request, system_id, interface_id):
        """Link interface to a subnet.

        :param mode: AUTO, DHCP, STATIC or LINK_UP connection to subnet.
        :param subnet: Subnet linked to interface.
        :param ip_address: IP address for the interface in subnet. Only used
            when mode is STATIC. If not provided an IP address from subnet
            will be auto selected.
        :param default_gateway: True sets the gateway IP address for the subnet
            as the default gateway for the node this interface belongs to.
            Option can only be used with the AUTO and STATIC modes.

        Mode definitions:
        AUTO - Assign this interface a static IP address from the provided
        subnet. The subnet must be a managed subnet. The IP address will
        not be assigned until the node goes to be deployed.

        DHCP - Bring this interface up with DHCP on the given subnet. Only
        one subnet can be set to DHCP. If the subnet is managed this
        interface will pull from the dynamic IP range.

        STATIC - Bring this interface up with a STATIC IP address on the
        given subnet. Any number of STATIC links can exist on an interface.

        LINK_UP - Bring this interface up only on the given subnet. No IP
        address will be assigned to this interface. The interface cannot
        have any current AUTO, DHCP or STATIC links.

        Returns 404 if the node or interface is not found.
        """
        interface = Interface.objects.get_interface_or_404(
            system_id, interface_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            interface.node, request.user, "link subnet")
        form = InterfaceLinkForm(instance=interface, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=False)
    def unlink_subnet(self, request, system_id, interface_id):
        """Unlink interface to a subnet.

        :param id: ID of the link on the interface to remove.

        Returns 404 if the node or interface is not found.
        """
        interface = Interface.objects.get_interface_or_404(
            system_id, interface_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            interface.node, request.user, "unlink subnet")
        form = InterfaceUnlinkForm(instance=interface, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    @operation(idempotent=False)
    def set_default_gateway(self, request, system_id, interface_id):
        """Set the node to use this interface as the default gateway.

        If this interface has more than one subnet with a gateway IP in the
        same IP address family then specifying the ID of the link on
        this interface is required.

        :param link_id: ID of the link on this interface to select the
            default gateway IP address from.

        Returns 400 if the interface has not AUTO or STATIC links.
        Returns 404 if the node or interface is not found.
        """
        interface = Interface.objects.get_interface_or_404(
            system_id, interface_id, request.user, NODE_PERMISSION.ADMIN)
        raise_error_for_invalid_state_on_allocated_operations(
            interface.node, request.user, "set default gateway")
        form = InterfaceSetDefaultGatwayForm(
            instance=interface, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)


class PhysicalInterfaceHandler(NodeInterfaceHandler):
    """
    This handler only exists because piston requires a unique handler per
    class type. Without this class the resource_uri will not be added to any
    object that is of type `PhysicalInterface` when it is emitted from the
    `NodeInterfaceHandler`.

    Important: This should not be used in the urls_api.py. This is only here
        to support piston.
    """
    hidden = True
    model = PhysicalInterface


class BondInterfaceHandler(NodeInterfaceHandler):
    """
    This handler only exists because piston requires a unique handler per
    class type. Without this class the resource_uri will not be added to any
    object that is of type `BondInterface` when it is emitted from the
    `NodeInterfaceHandler`.

    Important: This should not be used in the urls_api.py. This is only here
        to support piston.
    """
    hidden = True
    model = BondInterface


class VLANInterfaceHandler(NodeInterfaceHandler):
    """
    This handler only exists because piston requires a unique handler per
    class type. Without this class the resource_uri will not be added to any
    object that is of type `VLANInterface` when it is emitted from the
    `NodeInterfaceHandler`.

    Important: This should not be used in the urls_api.py. This is only here
        to support piston.
    """
    hidden = True
    model = VLANInterface
