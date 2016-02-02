# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for node power status query when state changes."""

__all__ = []

import random

from maasserver.models.signals import power
from maasserver.node_status import (
    get_failed_status,
    NODE_STATUS,
)
from maasserver.rpc.testing.fixtures import MockLiveRegionToClusterRPCFixture
from maasserver.testing.eventloop import (
    RegionEventLoopFixture,
    RunningEventLoopFixture,
)
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.testcase import (
    MAASServerTestCase,
    MAASTransactionServerTestCase,
)
from maasserver.utils.orm import (
    post_commit_hooks,
    transactional,
)
from maasserver.utils.threads import deferToDatabase
from maastesting.matchers import (
    MockCalledOnceWith,
    MockNotCalled,
)
from maastesting.twisted import always_succeed_with
from provisioningserver.power.poweraction import (
    PowerActionFail,
    UnknownPowerType,
)
from provisioningserver.rpc import cluster as cluster_module
from provisioningserver.rpc.exceptions import (
    NoConnectionsAvailable,
    PowerActionAlreadyInProgress,
)
from testtools.matchers import (
    Equals,
    Is,
)
from twisted.internet.task import Clock


class TestStatusQueryEvent(MAASServerTestCase):

    def test_changing_status_of_node_emits_event(self):
        self.patch_autospec(power, 'update_power_state_of_node_soon')
        old_status = NODE_STATUS.COMMISSIONING
        node = factory.make_Node(status=old_status, power_type='virsh')
        node.status = get_failed_status(old_status)
        node.save()
        # update_power_state_of_node_soon is registered as a post-commit task,
        # so it's not called immediately.
        self.expectThat(
            power.update_power_state_of_node_soon,
            MockNotCalled())
        # One post-commit hooks have been fired, then it's called.
        post_commit_hooks.fire()
        self.assertThat(
            power.update_power_state_of_node_soon,
            MockCalledOnceWith(node.system_id))

    def test_changing_not_tracked_status_of_node_doesnt_emit_event(self):
        self.patch_autospec(power, "update_power_state_of_node_soon")
        old_status = NODE_STATUS.ALLOCATED
        node = factory.make_Node(status=old_status, power_type="virsh")
        node.status = NODE_STATUS.DEPLOYING
        node.save()
        self.assertThat(
            power.update_power_state_of_node_soon,
            MockNotCalled())


class TestUpdatePowerStateOfNodeSoon(MAASServerTestCase):

    def test__calls_update_power_state_of_node_after_wait_time(self):
        self.patch_autospec(power, "update_power_state_of_node")
        node = factory.make_Node(power_type="virsh")
        clock = Clock()
        power.update_power_state_of_node_soon(node.system_id, clock=clock)
        self.assertThat(
            power.update_power_state_of_node,
            MockNotCalled())
        clock.advance(power.WAIT_TO_QUERY.total_seconds())
        self.assertThat(
            power.update_power_state_of_node,
            MockCalledOnceWith(node.system_id))


class TestUpdatePowerStateOfNode(MAASTransactionServerTestCase):

    def prepare_rpc(self, rack_controller, side_effect):
        self.useFixture(RegionEventLoopFixture("rpc"))
        self.useFixture(RunningEventLoopFixture())
        self.rpc_fixture = self.useFixture(MockLiveRegionToClusterRPCFixture())
        protocol = self.rpc_fixture.makeCluster(
            rack_controller, cluster_module.PowerQuery)
        protocol.PowerQuery.side_effect = side_effect

    def test__updates_node_power_state(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", bmc_connected_to=rack_controller)
        random_state = random.choice(["on", "off"])
        self.prepare_rpc(
            rack_controller,
            side_effect=always_succeed_with({"state": random_state}))
        self.assertThat(
            power.update_power_state_of_node(node.system_id),
            Equals(random_state))
        self.assertThat(
            reload_object(node).power_state,
            Equals(random_state))

    def test__handles_already_deleted_node(self):
        node = factory.make_Node(power_type="virsh")
        node.delete()
        self.assertThat(
            power.update_power_state_of_node(node.system_id),
            Is(None))  # Denotes that nothing happened.

    def test__handles_node_being_deleted_in_the_middle(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", power_state="off",
            bmc_connected_to=rack_controller)
        self.prepare_rpc(
            rack_controller,
            side_effect=always_succeed_with({"state": "on"}))

        def delete_node_then_get_client(uuid):
            from maasserver.rpc import getClientFromIdentifiers
            d = deferToDatabase(transactional(node.delete))
            d.addCallback(lambda _: getClientFromIdentifiers(uuid))
            return d

        getClientFromIdentifiers = self.patch_autospec(
            power, "getClientFromIdentifiers")
        getClientFromIdentifiers.side_effect = delete_node_then_get_client

        self.assertThat(
            power.update_power_state_of_node(node.system_id),
            Is(None))  # Denotes that nothing happened.

    def test__updates_power_state_to_unknown_on_UnknownPowerType(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", bmc_connected_to=rack_controller)
        self.prepare_rpc(rack_controller, side_effect=UnknownPowerType())
        self.expectThat(
            power.update_power_state_of_node(node.system_id),
            Equals("unknown"))
        self.expectThat(
            reload_object(node).power_state,
            Equals("unknown"))

    def test__updates_power_state_to_unknown_on_NotImplementedError(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", bmc_connected_to=rack_controller)
        self.prepare_rpc(rack_controller, side_effect=NotImplementedError())
        self.expectThat(
            power.update_power_state_of_node(node.system_id),
            Equals("unknown"))
        self.expectThat(
            reload_object(node).power_state,
            Equals("unknown"))

    def test__does_nothing_on_PowerActionAlreadyInProgress(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", power_state="off",
            bmc_connected_to=rack_controller)
        self.prepare_rpc(
            rack_controller, side_effect=PowerActionAlreadyInProgress())
        self.expectThat(
            power.update_power_state_of_node(node.system_id),
            Is(None))  # Denotes that nothing happened.
        self.expectThat(
            reload_object(node).power_state,
            Equals("off"))

    def test__does_nothing_on_NoConnectionsAvailable(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", power_state="off",
            bmc_connected_to=rack_controller)
        self.prepare_rpc(rack_controller, side_effect=None)
        getClientFromIdentifiers = self.patch_autospec(
            power, "getClientFromIdentifiers")
        getClientFromIdentifiers.side_effect = NoConnectionsAvailable()
        self.expectThat(
            power.update_power_state_of_node(node.system_id),
            Is(None))  # Denotes that nothing happened.
        self.expectThat(
            reload_object(node).power_state,
            Equals("off"))

    def test__updates_power_state_to_error_on_PowerActionFail(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", bmc_connected_to=rack_controller)
        self.prepare_rpc(rack_controller, side_effect=PowerActionFail())
        self.expectThat(
            power.update_power_state_of_node(node.system_id),
            Equals("error"))
        self.expectThat(
            reload_object(node).power_state,
            Equals("error"))

    def test__updates_power_state_to_error_on_other_error(self):
        rack_controller = factory.make_RackController()
        node = factory.make_Node(
            power_type="virsh", bmc_connected_to=rack_controller)
        self.prepare_rpc(rack_controller, side_effect=factory.make_exception())
        self.assertThat(
            power.update_power_state_of_node(node.system_id),
            Equals("error"))
        self.expectThat(
            reload_object(node).power_state,
            Equals("error"))
