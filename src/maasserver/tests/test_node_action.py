# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for node actions."""

__all__ = []

from unittest.mock import ANY

from django.db import transaction
from maasserver import locks
from maasserver.clusterrpc.boot_images import RackControllersImporter
from maasserver.clusterrpc.utils import get_error_message_for_exception
from maasserver.enum import (
    INTERFACE_TYPE,
    IPADDRESS_TYPE,
    NODE_PERMISSION,
    NODE_STATUS,
    NODE_STATUS_CHOICES,
    NODE_STATUS_CHOICES_DICT,
    NODE_TYPE,
    NODE_TYPE_CHOICES,
    POWER_STATE,
)
from maasserver.exceptions import NodeActionError
from maasserver.models import (
    signals,
    StaticIPAddress,
)
from maasserver.models.node import typecast_to_node_type
from maasserver.models.signals.testing import SignalsDisabled
from maasserver.node_action import (
    Abort,
    Acquire,
    Commission,
    compile_node_actions,
    Delete,
    Deploy,
    ImportImages,
    MarkBroken,
    MarkFixed,
    NodeAction,
    PowerOff,
    PowerOn,
    Release,
    RPC_EXCEPTIONS,
    SetZone,
)
from maasserver.node_status import (
    MONITORED_STATUSES,
    NON_MONITORED_STATUSES,
)
from maasserver.testing.factory import factory
from maasserver.testing.osystems import (
    make_osystem_with_releases,
    make_usable_osystem,
)
from maasserver.testing.testcase import (
    MAASServerTestCase,
    MAASTransactionServerTestCase,
)
from maasserver.utils.orm import (
    post_commit,
    post_commit_hooks,
    reload_object,
)
from maastesting.matchers import (
    MockCalledOnce,
    MockCalledOnceWith,
)
from metadataserver.enum import RESULT_TYPE
from metadataserver.models.noderesult import NodeResult
from netaddr import IPNetwork
from provisioningserver.utils.shell import ExternalProcessError
from testtools.matchers import Equals


ALL_STATUSES = list(NODE_STATUS_CHOICES_DICT)


class FakeNodeAction(NodeAction):
    name = "fake"
    display = "Action label"
    actionable_statuses = ALL_STATUSES
    permission = NODE_PERMISSION.VIEW
    for_type = [NODE_TYPE.MACHINE]

    # For testing: an inhibition for inhibit() to return.
    fake_inhibition = None

    def inhibit(self):
        return self.fake_inhibition

    def execute(self):
        pass


class TestNodeAction(MAASServerTestCase):

    def test_compile_node_actions_returns_available_actions(self):

        class MyAction(FakeNodeAction):
            name = factory.make_string()

        actions = compile_node_actions(
            factory.make_Node(), factory.make_admin(), classes=[MyAction])
        self.assertEqual([MyAction.name], list(actions.keys()))

    def test_compile_node_actions_checks_node_status(self):

        class MyAction(FakeNodeAction):
            actionable_statuses = (NODE_STATUS.READY, )

        node = factory.make_Node(status=NODE_STATUS.NEW)
        actions = compile_node_actions(
            node, factory.make_admin(), classes=[MyAction])
        self.assertEqual({}, actions)

    def test_compile_node_actions_checks_permission(self):

        class MyAction(FakeNodeAction):
            permission = NODE_PERMISSION.EDIT

        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        actions = compile_node_actions(
            node, factory.make_User(), classes=[MyAction])
        self.assertEqual({}, actions)

    def test_compile_node_actions_includes_inhibited_actions(self):

        class MyAction(FakeNodeAction):
            fake_inhibition = factory.make_string()

        actions = compile_node_actions(
            factory.make_Node(), factory.make_admin(), classes=[MyAction])
        self.assertEqual([MyAction.name], list(actions.keys()))

    def test_compile_node_actions_maps_names(self):

        class Action1(FakeNodeAction):
            name = factory.make_string()

        class Action2(FakeNodeAction):
            name = factory.make_string()

        actions = compile_node_actions(
            factory.make_Node(), factory.make_admin(),
            classes=[Action1, Action2])
        for name, action in actions.items():
            self.assertEqual(name, action.name)

    def test_compile_node_actions_maintains_order(self):
        names = [factory.make_string() for counter in range(4)]
        classes = [
            type("Action%d" % counter, (FakeNodeAction,), {'name': name})
            for counter, name in enumerate(names)]
        actions = compile_node_actions(
            factory.make_Node(), factory.make_admin(), classes=classes)
        self.assertSequenceEqual(names, list(actions.keys()))
        self.assertSequenceEqual(
            names, [action.name for action in list(actions.values())])

    def test_is_permitted_allows_if_user_has_permission(self):

        class MyAction(FakeNodeAction):
            permission = NODE_PERMISSION.EDIT

        node = factory.make_Node(
            status=NODE_STATUS.ALLOCATED, owner=factory.make_User())
        self.assertTrue(MyAction(node, node.owner).is_permitted())

    def test_is_permitted_disallows_if_user_lacks_permission(self):

        class MyAction(FakeNodeAction):
            permission = NODE_PERMISSION.EDIT

        node = factory.make_Node(
            status=NODE_STATUS.ALLOCATED, owner=factory.make_User())
        self.assertFalse(MyAction(node, factory.make_User()).is_permitted())

    def test_is_permitted_uses_node_permission(self):

        class MyAction(FakeNodeAction):
            permission = NODE_PERMISSION.VIEW
            node_permission = NODE_PERMISSION.EDIT

        node = factory.make_Node(
            status=NODE_STATUS.ALLOCATED, owner=factory.make_User())
        self.assertFalse(MyAction(node, factory.make_User()).is_permitted())

    def test_is_permitted_doest_use_node_permission_if_device(self):

        class MyAction(FakeNodeAction):
            permission = NODE_PERMISSION.VIEW
            node_permission = NODE_PERMISSION.EDIT

        node = factory.make_Node(
            status=NODE_STATUS.ALLOCATED, owner=factory.make_User(),
            node_type=NODE_TYPE.DEVICE)
        self.assertTrue(MyAction(node, factory.make_User()).is_permitted())

    def test_inhibition_wraps_inhibit(self):
        inhibition = factory.make_string()
        action = FakeNodeAction(factory.make_Node(), factory.make_User())
        action.fake_inhibition = inhibition
        self.assertEqual(inhibition, action.inhibition)

    def test_inhibition_caches_inhibition(self):
        # The inhibition property will call inhibit() only once.  We can
        # prove this by changing the string inhibit() returns; it won't
        # affect the value of the property.
        inhibition = factory.make_string()
        action = FakeNodeAction(factory.make_Node(), factory.make_User())
        action.fake_inhibition = inhibition
        self.assertEqual(inhibition, action.inhibition)
        action.fake_inhibition = factory.make_string()
        self.assertEqual(inhibition, action.inhibition)

    def test_inhibition_caches_None(self):
        # An inhibition of None is also faithfully cached.  In other
        # words, it doesn't get mistaken for an uninitialized cache or
        # anything.
        action = FakeNodeAction(factory.make_Node(), factory.make_User())
        action.fake_inhibition = None
        self.assertIsNone(action.inhibition)
        action.fake_inhibition = factory.make_string()
        self.assertIsNone(action.inhibition)

    def test_node_only_is_not_actionable_if_node_isnt_node_type(self):
        status = NODE_STATUS.NEW
        owner = factory.make_User()
        node = factory.make_Node(
            owner=owner, status=status, node_type=NODE_TYPE.DEVICE)
        action = FakeNodeAction(node, owner)
        action.node_only = True
        self.assertFalse(action.is_actionable())

    def test_node_only_is_actionable_if_node_type_is_node(self):
        status = NODE_STATUS.NEW
        owner = factory.make_User()
        node = factory.make_Node(
            owner=owner, status=status, node_type=NODE_TYPE.MACHINE)
        action = FakeNodeAction(node, owner)
        action.node_only = True
        self.assertTrue(action.is_actionable())

    def test_is_actionable_checks_node_status_in_actionable_status(self):

        class MyAction(FakeNodeAction):
            actionable_statuses = [NODE_STATUS.ALLOCATED]

        node = factory.make_Node(status=NODE_STATUS.BROKEN)
        self.assertFalse(MyAction(node, factory.make_User()).is_actionable())

    def test_is_actionable_checks_permission(self):

        class MyAction(FakeNodeAction):
            node_permission = NODE_PERMISSION.ADMIN

        node = factory.make_Node()
        self.assertFalse(MyAction(node, factory.make_User()).is_actionable())


class TestDeleteAction(MAASServerTestCase):

    def test__deletes_node(self):
        node = factory.make_Node()
        action = Delete(node, factory.make_admin())
        action.execute()
        self.assertIsNone(reload_object(node))


class TestCommissionAction(MAASServerTestCase):

    scenarios = (
        ("NEW", {"status": NODE_STATUS.NEW}),
        ("FAILED_COMMISSIONING", {
            "status": NODE_STATUS.FAILED_COMMISSIONING}),
        ("READY", {"status": NODE_STATUS.READY}),
    )

    def test_raise_NodeActionError_if_on(self):
        node = factory.make_Node(
            status=self.status, power_state=POWER_STATE.ON)
        user = factory.make_admin()
        action = Commission(node, user)
        self.assertTrue(action.is_permitted())
        self.assertRaises(NodeActionError, action.execute)

    def test_Commission_starts_commissioning(self):
        node = factory.make_Node(
            interface=True, status=self.status,
            power_type='manual', power_state=POWER_STATE.OFF)
        node_start = self.patch(node, '_start')
        node_start.side_effect = (
            lambda user, user_data, old_status: post_commit())
        admin = factory.make_admin()
        action = Commission(node, admin)
        with post_commit_hooks:
            action.execute()
        self.assertEqual(NODE_STATUS.COMMISSIONING, node.status)
        self.assertThat(
            node_start, MockCalledOnceWith(admin, ANY, ANY))


class TestAbortAction(MAASTransactionServerTestCase):

    def test_Abort_aborts_disk_erasing(self):
        self.useFixture(SignalsDisabled("power"))
        with transaction.atomic():
            owner = factory.make_User()
            node = factory.make_Node(
                status=NODE_STATUS.DISK_ERASING, owner=owner)

        node_stop = self.patch_autospec(node, '_stop')
        # Return a post-commit hook from Node.stop().
        node_stop.side_effect = lambda user: post_commit()

        with post_commit_hooks:
            with transaction.atomic():
                Abort(node, owner).execute()

        with transaction.atomic():
            node = reload_object(node)
            self.assertEqual(NODE_STATUS.FAILED_DISK_ERASING, node.status)

        self.assertThat(node_stop, MockCalledOnceWith(owner))

    def test_Abort_aborts_commissioning(self):
        """Makes sure a COMMISSIONING node is returned to NEW status after an
        abort.
        """
        with transaction.atomic():
            node = factory.make_Node(
                interface=True, status=NODE_STATUS.COMMISSIONING,
                power_type='virsh')
            admin = factory.make_admin()

        node_stop = self.patch_autospec(node, '_stop')
        # Return a post-commit hook from Node.stop().
        node_stop.side_effect = lambda user: post_commit()

        with post_commit_hooks:
            with transaction.atomic():
                Abort(node, admin).execute()

        with transaction.atomic():
            node = reload_object(node)
            self.assertEqual(NODE_STATUS.NEW, node.status)

        self.assertThat(node_stop, MockCalledOnceWith(admin))

    def test_Abort_aborts_deployment(self):
        """Makes sure a DEPLOYING node is returned to ALLOCATED status after an
        abort.
        """
        with transaction.atomic():
            node = factory.make_Node(
                interface=True, status=NODE_STATUS.DEPLOYING,
                power_type='virsh')
            admin = factory.make_admin()

        node_stop = self.patch_autospec(node, '_stop')
        # Return a post-commit hook from Node.stop().
        node_stop.side_effect = lambda user: post_commit()

        with post_commit_hooks:
            with transaction.atomic():
                Abort(node, admin).execute()

        with transaction.atomic():
            node = reload_object(node)
            self.assertEqual(NODE_STATUS.ALLOCATED, node.status)

        self.assertThat(node_stop, MockCalledOnceWith(admin))


class TestAcquireNodeAction(MAASServerTestCase):

    def test_Acquire_acquires_node(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.READY,
            power_type='manual', with_boot_disk=True)
        user = factory.make_User()
        Acquire(node, user).execute()
        self.assertEqual(NODE_STATUS.ALLOCATED, node.status)
        self.assertEqual(user, node.owner)

    def test_Acquire_uses_node_acquire_lock(self):
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.READY,
            power_type='manual', with_boot_disk=True)
        user = factory.make_User()
        node_acquire = self.patch(locks, 'node_acquire')
        Acquire(node, user).execute()
        self.assertThat(node_acquire.__enter__, MockCalledOnceWith())
        self.assertThat(
            node_acquire.__exit__, MockCalledOnceWith(None, None, None))


class TestDeployAction(MAASServerTestCase):

    def test_Deploy_inhibit_allows_user_with_SSH_key(self):
        user_with_key = factory.make_User()
        factory.make_SSHKey(user_with_key)
        self.assertIsNone(
            Deploy(factory.make_Node(), user_with_key).inhibit())

    def test_Deploy_inhibit_allows_user_without_SSH_key(self):
        user_without_key = factory.make_User()
        action = Deploy(factory.make_Node(), user_without_key)
        inhibition = action.inhibit()
        self.assertIsNone(inhibition)

    def test_Deploy_is_actionable_if_user_doesnt_have_ssh_keys(self):
        owner = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=owner)
        self.assertTrue(Deploy(node, owner).is_actionable())

    def test_Deploy_is_actionable_if_user_has_ssh_keys(self):
        owner = factory.make_User()
        factory.make_SSHKey(owner)
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=owner)
        self.assertTrue(Deploy(node, owner).is_actionable())

    def test_Deploy_starts_node(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        node_start = self.patch(node, 'start')
        Deploy(node, user).execute()
        self.assertThat(
            node_start, MockCalledOnceWith(user))

    def test_Deploy_raises_NodeActionError_for_invalid_os(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        self.patch(node, 'start')
        os_name = factory.make_name("os")
        release_name = factory.make_name("release")
        extra = {
            "osystem": os_name,
            "distro_series": release_name,
        }
        error = self.assertRaises(
            NodeActionError, Deploy(node, user).execute, **extra)
        self.assertEqual(
            "['%s is not a support operating system.']" % os_name,
            str(error))

    def test_Deploy_sets_osystem_and_series(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        self.patch(node, 'start')
        osystem = make_usable_osystem(self)
        os_name = osystem["name"]
        release_name = osystem["releases"][0]["name"]
        extra = {
            "osystem": os_name,
            "distro_series": release_name
        }
        Deploy(node, user).execute(**extra)
        self.expectThat(node.osystem, Equals(os_name))
        self.expectThat(
            node.distro_series, Equals(release_name))

    def test_Deploy_sets_osystem_and_series_strips_license_key_token(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        self.patch(node, 'start')
        osystem = make_usable_osystem(self)
        os_name = osystem["name"]
        release_name = osystem["releases"][0]["name"]
        extra = {
            "osystem": os_name,
            "distro_series": release_name + '*'
        }
        Deploy(node, user).execute(**extra)
        self.expectThat(node.osystem, Equals(os_name))
        self.expectThat(
            node.distro_series, Equals(release_name))

    def test_Deploy_doesnt_set_osystem_and_series_if_os_missing(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        self.patch(node, 'start')
        osystem = make_osystem_with_releases(self)
        extra = {
            "distro_series": osystem["releases"][0]["name"],
        }
        Deploy(node, user).execute(**extra)
        self.expectThat(node.osystem, Equals(""))
        self.expectThat(node.distro_series, Equals(""))

    def test_Deploy_doesnt_set_osystem_and_series_if_series_missing(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        self.patch(node, 'start')
        osystem = make_osystem_with_releases(self)
        extra = {
            "osystem": osystem["name"],
        }
        Deploy(node, user).execute(**extra)
        self.expectThat(node.osystem, Equals(""))
        self.expectThat(node.distro_series, Equals(""))

    def test_Deploy_allocates_node_if_node_not_already_allocated(self):
        user = factory.make_User()
        node = factory.make_Node(status=NODE_STATUS.READY, with_boot_disk=True)
        self.patch(node, 'start')
        action = Deploy(node, user)
        action.execute()

        self.assertEqual(user, node.owner)
        self.assertEqual(NODE_STATUS.ALLOCATED, node.status)


class TestDeployActionTransactional(MAASTransactionServerTestCase):
    """The following TestDeployAction tests require
        MAASTransactionServerTestCase, and thus, have been separated
        from the TestDeployAction above.
    """

    def test_Deploy_returns_error_when_no_more_static_IPs(self):
        user = factory.make_User()
        network = IPNetwork("10.0.0.0/30")
        subnet = factory.make_Subnet(cidr=str(network.cidr))
        rack_controller = factory.make_RackController()
        subnet.vlan.dhcp_on = True
        subnet.vlan.primary_rack = rack_controller
        subnet.vlan.save()
        node = factory.make_Node(
            status=NODE_STATUS.ALLOCATED, power_type='virsh', owner=user,
            power_state=POWER_STATE.OFF, bmc_connected_to=rack_controller)
        interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node, vlan=subnet.vlan)
        factory.make_StaticIPAddress(
            alloc_type=IPADDRESS_TYPE.AUTO, ip="", subnet=subnet,
            interface=interface)

        # Pre-claim the only addresses.
        with transaction.atomic():
            StaticIPAddress.objects.allocate_new(
                subnet, requested_address="10.0.0.1")
            StaticIPAddress.objects.allocate_new(
                subnet, requested_address="10.0.0.2")
            StaticIPAddress.objects.allocate_new(
                subnet, requested_address="10.0.0.3")

        e = self.assertRaises(NodeActionError, Deploy(node, user).execute)
        self.expectThat(
            str(e), Equals(
                "%s: Failed to start, static IP addresses are exhausted." %
                node.hostname))
        self.assertEqual(NODE_STATUS.ALLOCATED, node.status)


class TestSetZoneAction(MAASServerTestCase):

    def test_SetZone_sets_zone(self):
        user = factory.make_User()
        zone1 = factory.make_Zone()
        zone2 = factory.make_Zone()
        node = factory.make_Node(status=NODE_STATUS.NEW, zone=zone1)
        action = SetZone(node, user)
        action.execute(zone_id=zone2.id)
        self.assertEqual(node.zone.id, zone2.id)


class TestPowerOnAction(MAASServerTestCase):

    def test_PowerOn_starts_node(self):
        user = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.ALLOCATED,
            power_type='manual', owner=user)
        node_start = self.patch(node, 'start')
        PowerOn(node, user).execute()
        self.assertThat(
            node_start, MockCalledOnceWith(user))

    def test_PowerOn_requires_edit_permission(self):
        user = factory.make_User()
        node = factory.make_Node()
        self.assertFalse(
            user.has_perm(NODE_PERMISSION.EDIT, node))
        self.assertFalse(PowerOn(node, user).is_permitted())

    def test_PowerOn_is_actionable_if_node_doesnt_have_an_owner(self):
        owner = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DEPLOYED,
            power_type='manual')
        self.assertTrue(PowerOn(node, owner).is_actionable())

    def test_PowerOn_is_actionable_if_node_does_have_an_owner(self):
        owner = factory.make_User()
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DEPLOYED,
            power_type='manual', owner=owner)
        self.assertTrue(PowerOn(node, owner).is_actionable())


class TestPowerOffAction(MAASServerTestCase):

    def test__stops_deployed_node(self):
        user = factory.make_User()
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.DEPLOYED,
            power_type='ipmi',
            owner=user, power_parameters=params)
        node_stop = self.patch_autospec(node, 'stop')

        PowerOff(node, user).execute()

        self.assertThat(node_stop, MockCalledOnceWith(user))

    def test__stops_Ready_node(self):
        admin = factory.make_admin()
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        node = factory.make_Node(
            interface=True, status=NODE_STATUS.READY,
            power_type='ipmi', power_parameters=params)
        node_stop = self.patch_autospec(node, 'stop')

        PowerOff(node, admin).execute()

        self.assertThat(node_stop, MockCalledOnceWith(admin))

    def test__actionable_for_non_monitored_states(self):
        all_statuses = NON_MONITORED_STATUSES
        results = {}
        for status in all_statuses:
            node = factory.make_Node(
                status=status, power_type='ipmi', power_state=POWER_STATE.ON)
            actions = compile_node_actions(
                node, factory.make_admin(), classes=[PowerOff])
            results[status] = list(actions.keys())
        expected_results = {status: [PowerOff.name] for status in all_statuses}
        self.assertEqual(
            expected_results, results,
            "Nodes with certain statuses could not be powered off.")

    def test__non_actionable_for_monitored_states(self):
        all_statuses = MONITORED_STATUSES
        results = {}
        for status in all_statuses:
            node = factory.make_Node(
                status=status, power_type='ipmi', power_state=POWER_STATE.ON)
            actions = compile_node_actions(
                node, factory.make_admin(), classes=[PowerOff])
            results[status] = list(actions.keys())
        expected_results = {status: [] for status in all_statuses}
        self.assertEqual(
            expected_results, results,
            "Nodes with certain statuses could be powered off.")

    def test__non_actionable_if_node_already_off(self):
        all_statuses = NON_MONITORED_STATUSES
        results = {}
        for status in all_statuses:
            node = factory.make_Node(
                status=status, power_type='ipmi', power_state=POWER_STATE.OFF)
            actions = compile_node_actions(
                node, factory.make_admin(), classes=[PowerOff])
            results[status] = list(actions.keys())
        expected_results = {status: [] for status in all_statuses}
        self.assertEqual(
            expected_results, results,
            "Nodes already powered off can be powered off.")


ACTIONABLE_STATUSES = [
    NODE_STATUS.DEPLOYING,
    NODE_STATUS.FAILED_DEPLOYMENT,
    NODE_STATUS.FAILED_DISK_ERASING,
]


class TestReleaseAction(MAASServerTestCase):

    scenarios = [
        (NODE_STATUS_CHOICES_DICT[status], dict(actionable_status=status))
        for status in ACTIONABLE_STATUSES
    ]

    def test_Release_stops_and_releases_node(self):
        user = factory.make_User()
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        node = factory.make_Node(
            interface=True, status=self.actionable_status,
            power_type='ipmi', power_state=POWER_STATE.ON,
            owner=user, power_parameters=params)
        node_stop = self.patch_autospec(node, '_stop')

        with post_commit_hooks:
            Release(node, user).execute()

        self.expectThat(node.status, Equals(NODE_STATUS.RELEASING))
        self.assertThat(
            node_stop, MockCalledOnceWith(user))

    def test_Release_enters_disk_erasing(self):
        user = factory.make_User()
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        node = factory.make_Node(
            interface=True, status=self.actionable_status,
            power_type='ipmi', power_state=POWER_STATE.OFF,
            owner=user, power_parameters=params)
        old_status = node.status
        node_start = self.patch_autospec(node, '_start')
        node_start.return_value = None

        with post_commit_hooks:
            Release(node, user).execute(erase=True)

        self.expectThat(node.status, Equals(NODE_STATUS.DISK_ERASING))
        self.assertThat(
            node_start, MockCalledOnceWith(
                user, user_data=ANY, old_status=old_status))

    def test_Release_passes_secure_erase_and_quick_erase(self):
        user = factory.make_User()
        params = dict(
            power_address=factory.make_ipv4_address(),
            power_user=factory.make_string(),
            power_pass=factory.make_string())
        node = factory.make_Node(
            interface=True, status=self.actionable_status,
            power_type='ipmi', power_state=POWER_STATE.OFF,
            owner=user, power_parameters=params)
        node_release_or_erase = self.patch_autospec(node, 'release_or_erase')

        with post_commit_hooks:
            Release(node, user).execute(
                erase=True, secure_erase=True, quick_erase=True)

        self.assertThat(
            node_release_or_erase,
            MockCalledOnceWith(
                user, erase=True, secure_erase=True, quick_erase=True))


class TestMarkBrokenAction(MAASServerTestCase):

    def test_changes_status(self):
        user = factory.make_User()
        node = factory.make_Node(owner=user, status=NODE_STATUS.COMMISSIONING)
        action = MarkBroken(node, user)
        self.assertTrue(action.is_permitted())
        action.execute()
        self.assertEqual(NODE_STATUS.BROKEN, reload_object(node).status)

    def test_updates_error_description(self):
        user = factory.make_User()
        node = factory.make_Node(owner=user, status=NODE_STATUS.COMMISSIONING)
        action = MarkBroken(node, user)
        self.assertTrue(action.is_permitted())
        action.execute()
        self.assertEqual(
            "via web interface",
            reload_object(node).error_description
        )

    def test_requires_edit_permission(self):
        user = factory.make_User()
        node = factory.make_Node()
        self.assertFalse(MarkBroken(node, user).is_permitted())


class TestMarkFixedAction(MAASServerTestCase):

    def make_commissioning_data(self, node, result=0, count=3):
        return [
            NodeResult.objects.create(
                node=node, name=factory.make_name(), script_result=result,
                result_type=RESULT_TYPE.COMMISSIONING)
            for _ in range(count)
            ]

    def test_changes_status(self):
        node = factory.make_Node(
            status=NODE_STATUS.BROKEN, power_state=POWER_STATE.OFF)
        self.make_commissioning_data(node)
        user = factory.make_admin()
        action = MarkFixed(node, user)
        self.assertTrue(action.is_permitted())
        action.execute()
        self.assertEqual(NODE_STATUS.READY, reload_object(node).status)

    def test_raise_NodeActionError_if_on(self):
        node = factory.make_Node(
            status=NODE_STATUS.BROKEN, power_state=POWER_STATE.ON)
        user = factory.make_admin()
        action = MarkFixed(node, user)
        self.assertTrue(action.is_permitted())
        self.assertRaises(NodeActionError, action.execute)

    def test_raise_NodeActionError_if_no_commissioning_results(self):
        node = factory.make_Node(
            status=NODE_STATUS.BROKEN, power_state=POWER_STATE.OFF)
        user = factory.make_admin()
        action = MarkFixed(node, user)
        self.assertTrue(action.is_permitted())
        self.assertRaises(NodeActionError, action.execute)

    def test_raise_NodeActionError_if_one_commissioning_result_fails(self):
        node = factory.make_Node(
            status=NODE_STATUS.BROKEN, power_state=POWER_STATE.OFF)
        self.make_commissioning_data(node)
        self.make_commissioning_data(node, result=1, count=1)
        user = factory.make_admin()
        action = MarkFixed(node, user)
        self.assertTrue(action.is_permitted())
        self.assertRaises(NodeActionError, action.execute)

    def test_raise_NodeActionError_if_multi_commissioning_result_fails(self):
        node = factory.make_Node(
            status=NODE_STATUS.BROKEN, power_state=POWER_STATE.OFF)
        self.make_commissioning_data(node)
        self.make_commissioning_data(node, result=1)
        user = factory.make_admin()
        action = MarkFixed(node, user)
        self.assertTrue(action.is_permitted())
        self.assertRaises(NodeActionError, action.execute)

    def test_requires_admin_permission(self):
        user = factory.make_User()
        node = factory.make_Node()
        self.assertFalse(MarkFixed(node, user).is_permitted())

    def test_not_enabled_if_not_broken(self):
        status = factory.pick_choice(
            NODE_STATUS_CHOICES, but_not=[NODE_STATUS.BROKEN])
        node = factory.make_Node(status=status)
        actions = compile_node_actions(
            node, factory.make_admin(), classes=[MarkFixed])
        self.assertItemsEqual([], actions)


class TestImportImages(MAASServerTestCase):

    def test_import_images(self):
        user = factory.make_admin()
        rack = factory.make_RackController()
        mock_import = self.patch(RackControllersImporter, 'schedule')

        with post_commit_hooks:
            ImportImages(rack, user).execute()

        self.assertThat(mock_import, MockCalledOnce())

    def test_requires_admin_permission(self):
        user = factory.make_User()
        rack = factory.make_RackController()
        self.assertFalse(ImportImages(rack, user).is_permitted())

    def test_requires_rack(self):
        user = factory.make_User()
        node = factory.make_Node(
            node_type=factory.pick_choice(
                NODE_TYPE_CHOICES, but_not=[
                    NODE_TYPE.RACK_CONTROLLER,
                    NODE_TYPE.REGION_AND_RACK_CONTROLLER]))
        self.assertFalse(ImportImages(node, user).is_actionable())


class TestActionsErrorHandling(MAASServerTestCase):
    """Tests for error handling in actions.

    This covers RPC exceptions and `ExternalProcessError`s.
    """
    exceptions = RPC_EXCEPTIONS + (ExternalProcessError,)
    scenarios = [
        (exception_class.__name__, {"exception_class": exception_class})
        for exception_class in exceptions
    ]

    def make_exception(self):
        if self.exception_class is ExternalProcessError:
            exception = self.exception_class(
                1, ["cmd"], factory.make_name("exception"))
        else:
            exception = self.exception_class(factory.make_name("exception"))
        return exception

    def patch_rpc_methods(self, node):
        exception = self.make_exception()
        self.patch(node, '_start').side_effect = exception
        self.patch(node, '_stop').side_effect = exception

    def make_action(
            self, action_class, node_status, power_state=None,
            node_type=NODE_TYPE.MACHINE):
        node = factory.make_Node(
            interface=True, status=node_status, power_type='manual',
            power_state=power_state, node_type=node_type)
        admin = factory.make_admin()
        return action_class(typecast_to_node_type(node), admin)

    def test_Commission_handles_rpc_errors(self):
        self.addCleanup(signals.power.signals.enable)
        signals.power.signals.disable()

        action = self.make_action(
            Commission, NODE_STATUS.READY, POWER_STATE.OFF)
        self.patch_rpc_methods(action.node)
        exception = self.assertRaises(NodeActionError, action.execute)
        self.assertEqual(
            get_error_message_for_exception(action.node._start.side_effect),
            str(exception))

    def test_Abort_handles_rpc_errors(self):
        action = self.make_action(
            Abort, NODE_STATUS.DISK_ERASING)
        self.patch_rpc_methods(action.node)
        exception = self.assertRaises(NodeActionError, action.execute)
        self.assertEqual(
            get_error_message_for_exception(action.node._stop.side_effect),
            str(exception))

    def test_PowerOn_handles_rpc_errors(self):
        action = self.make_action(PowerOn, NODE_STATUS.READY)
        self.patch_rpc_methods(action.node)
        exception = self.assertRaises(NodeActionError, action.execute)
        self.assertEqual(
            get_error_message_for_exception(action.node._start.side_effect),
            str(exception))

    def test_PowerOff_handles_rpc_errors(self):
        action = self.make_action(PowerOff, NODE_STATUS.DEPLOYED)
        self.patch_rpc_methods(action.node)
        exception = self.assertRaises(NodeActionError, action.execute)
        self.assertEqual(
            get_error_message_for_exception(action.node._stop.side_effect),
            str(exception))

    def test_Release_handles_rpc_errors(self):
        action = self.make_action(
            Release, NODE_STATUS.ALLOCATED, power_state=POWER_STATE.ON)
        self.patch_rpc_methods(action.node)
        exception = self.assertRaises(NodeActionError, action.execute)
        self.assertEqual(
            get_error_message_for_exception(action.node._stop.side_effect),
            str(exception))
