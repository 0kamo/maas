# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `provisioningserver.drivers.pod.lxd`."""

__all__ = []

from os.path import join
import random
from unittest.mock import Mock, PropertyMock, sentinel

from testtools.matchers import Equals, IsInstance, MatchesAll, MatchesStructure
from testtools.testcase import ExpectedException
from twisted.internet.defer import inlineCallbacks

from maastesting.factory import factory
from maastesting.matchers import MockCalledOnceWith
from maastesting.testcase import MAASTestCase, MAASTwistedRunTest
from provisioningserver.drivers.pod import (
    RequestedMachine,
    RequestedMachineBlockDevice,
    RequestedMachineInterface,
)
from provisioningserver.drivers.pod import Capabilities, DiscoveredPodHints
from provisioningserver.drivers.pod import lxd as lxd_module
from provisioningserver.maas_certificates import (
    MAAS_CERTIFICATE,
    MAAS_PRIVATE_KEY,
)
from provisioningserver.refresh.node_info_scripts import LXD_OUTPUT_NAME
from provisioningserver.rpc.exceptions import PodInvalidResources
from provisioningserver.utils import (
    debian_to_kernel_architecture,
    kernel_to_debian_architecture,
)


def make_requested_machine():
    block_devices = [
        RequestedMachineBlockDevice(
            size=random.randint(1024 ** 3, 4 * 1024 ** 3)
        )
    ]
    interfaces = [RequestedMachineInterface()]
    return RequestedMachine(
        hostname=factory.make_name("hostname"),
        architecture="amd64/generic",
        cores=random.randint(2, 4),
        memory=random.randint(1024, 4096),
        cpu_speed=random.randint(2000, 3000),
        block_devices=block_devices,
        interfaces=interfaces,
    )


class TestLXDByteSuffixes(MAASTestCase):
    def test_convert_lxd_byte_suffixes_with_integers(self):
        numbers = [
            random.randint(1, 10)
            for _ in range(len(lxd_module.LXD_BYTE_SUFFIXES))
        ]
        expected_results = [
            numbers[idx] * value
            for idx, value in enumerate(lxd_module.LXD_BYTE_SUFFIXES.values())
        ]
        actual_results = [
            lxd_module.convert_lxd_byte_suffixes(str(numbers[idx]) + key)
            for idx, key in enumerate(lxd_module.LXD_BYTE_SUFFIXES.keys())
        ]
        self.assertSequenceEqual(expected_results, actual_results)


class TestLXDPodDriver(MAASTestCase):

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    def test_missing_packages(self):
        driver = lxd_module.LXDPodDriver()
        missing = driver.detect_missing_packages()
        self.assertItemsEqual([], missing)

    def make_parameters_context(self):
        return {
            "power_address": "".join(
                [
                    factory.make_name("power_address"),
                    ":%s" % factory.pick_port(),
                ]
            ),
            "instance_name": factory.make_name("instance_name"),
            "password": factory.make_name("password"),
        }

    def make_parameters(self, context):
        return (
            context.get("power_address"),
            context.get("instance_name"),
            context.get("password"),
        )

    def test_get_url(self):
        driver = lxd_module.LXDPodDriver()
        context = {"power_address": factory.make_hostname()}

        # Test ip adds protocol and port
        self.assertEqual(
            join("https://", "%s:%d" % (context["power_address"], 8443)),
            driver.get_url(context),
        )

        # Test ip:port adds protocol
        context["power_address"] += ":1234"
        self.assertEqual(
            join("https://", "%s" % context["power_address"]),
            driver.get_url(context),
        )

        # Test protocol:ip adds port
        context["power_address"] = join("https://", factory.make_hostname())
        self.assertEqual(
            "%s:%d" % (context.get("power_address"), 8443),
            driver.get_url(context),
        )

        # Test protocol:ip:port doesn't do anything
        context["power_address"] += ":1234"
        self.assertEqual(context.get("power_address"), driver.get_url(context))

    @inlineCallbacks
    def test__get_client(self):
        context = self.make_parameters_context()
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        client.has_api_extension.return_value = True
        client.trusted = False
        driver = lxd_module.LXDPodDriver()
        endpoint = driver.get_url(context)
        returned_client = yield driver.get_client(None, context)
        self.assertThat(
            Client,
            MockCalledOnceWith(
                endpoint=endpoint,
                cert=(MAAS_CERTIFICATE, MAAS_PRIVATE_KEY),
                verify=False,
            ),
        )
        self.assertThat(
            client.authenticate, MockCalledOnceWith(context["password"])
        )
        self.assertEquals(client, returned_client)

    @inlineCallbacks
    def test_get_client_raises_error_when_not_trusted_and_no_password(self):
        context = self.make_parameters_context()
        context["password"] = None
        pod_id = factory.make_name("pod_id")
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        client.trusted = False
        driver = lxd_module.LXDPodDriver()
        error_msg = f"Pod {pod_id}: Certificate is not trusted and no password was given."
        with ExpectedException(lxd_module.LXDPodError, error_msg):
            yield driver.get_client(pod_id, context)

    @inlineCallbacks
    def test_get_client_raises_error_when_cannot_connect(self):
        context = self.make_parameters_context()
        pod_id = factory.make_name("pod_id")
        Client = self.patch(lxd_module, "Client")
        Client.side_effect = lxd_module.ClientConnectionFailed()
        driver = lxd_module.LXDPodDriver()
        error_msg = f"Pod {pod_id}: Failed to connect to the LXD REST API."
        with ExpectedException(lxd_module.LXDPodError, error_msg):
            yield driver.get_client(pod_id, context)

    @inlineCallbacks
    def test__get_machine(self):
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(driver, "get_client")
        client = Client.return_value
        mock_machine = Mock()
        client.virtual_machines.get.return_value = mock_machine
        returned_machine = yield driver.get_machine(None, context)
        self.assertThat(Client, MockCalledOnceWith(None, context))
        self.assertEquals(mock_machine, returned_machine)

    @inlineCallbacks
    def test_get_machine_raises_error_when_machine_not_found(self):
        context = self.make_parameters_context()
        pod_id = factory.make_name("pod_id")
        instance_name = context.get("instance_name")
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(driver, "get_client")
        client = Client.return_value
        client.virtual_machines.get.side_effect = lxd_module.NotFound("Error")
        error_msg = f"Pod {pod_id}: LXD VM {instance_name} not found."
        with ExpectedException(lxd_module.LXDPodError, error_msg):
            yield driver.get_machine(pod_id, context)

    @inlineCallbacks
    def test__power_on(self):
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        mock_machine = self.patch(driver, "get_machine").return_value
        mock_machine.status_code = 110
        yield driver.power_on(None, context)
        self.assertThat(mock_machine.start, MockCalledOnceWith())

    @inlineCallbacks
    def test__power_off(self):
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        mock_machine = self.patch(driver, "get_machine").return_value
        mock_machine.status_code = 103
        yield driver.power_off(None, context)
        self.assertThat(mock_machine.stop, MockCalledOnceWith())

    @inlineCallbacks
    def test__power_query(self):
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        mock_machine = self.patch(driver, "get_machine").return_value
        mock_machine.status_code = 103
        state = yield driver.power_query(None, context)
        self.assertThat(state, Equals("on"))

    @inlineCallbacks
    def test_power_query_raises_error_on_unknown_state(self):
        context = self.make_parameters_context()
        pod_id = factory.make_name("pod_id")
        driver = lxd_module.LXDPodDriver()
        mock_machine = self.patch(driver, "get_machine").return_value
        mock_machine.status_code = 106
        error_msg = f"Pod {pod_id}: Unknown power status code: {mock_machine.status_code}"
        with ExpectedException(lxd_module.LXDPodError, error_msg):
            yield driver.power_query(pod_id, context)

    @inlineCallbacks
    def test_discover_requires_client_to_have_vm_support(self):
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        client.has_api_extension.return_value = False
        error_msg = "Please upgrade your LXD host to *."
        with ExpectedException(lxd_module.LXDPodError, error_msg):
            yield driver.discover(None, context)
        self.assertThat(
            client.has_api_extension, MockCalledOnceWith("virtual-machines")
        )

    @inlineCallbacks
    def test__discover(self):
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        client.has_api_extension.return_value = True
        name = factory.make_name("hostname")
        client.host_info = {
            "environment": {
                "architectures": ["x86_64", "i686"],
                "kernel_architecture": "x86_64",
                "server_name": name,
            }
        }
        mac_address = factory.make_mac_address()
        client.resources = {
            "network": {"cards": [{"ports": [{"address": mac_address}]}]}
        }
        discovered_pod = yield driver.discover(None, context)
        self.assertItemsEqual(["amd64/generic"], discovered_pod.architectures)
        self.assertEquals(name, discovered_pod.name)
        self.assertItemsEqual([mac_address], discovered_pod.mac_addresses)
        self.assertEquals(-1, discovered_pod.cores)
        self.assertEquals(-1, discovered_pod.cpu_speed)
        self.assertEquals(-1, discovered_pod.memory)
        self.assertEquals(0, discovered_pod.local_storage)
        self.assertEquals(-1, discovered_pod.local_disks)
        self.assertEquals(-1, discovered_pod.iscsi_storage)
        self.assertEquals(-1, discovered_pod.hints.cores)
        self.assertEquals(-1, discovered_pod.hints.cpu_speed)
        self.assertEquals(-1, discovered_pod.hints.local_storage)
        self.assertEquals(-1, discovered_pod.hints.local_disks)
        self.assertEquals(-1, discovered_pod.hints.iscsi_storage)
        self.assertItemsEqual(
            [
                Capabilities.COMPOSABLE,
                Capabilities.DYNAMIC_LOCAL_STORAGE,
                Capabilities.OVER_COMMIT,
                Capabilities.STORAGE_POOLS,
            ],
            discovered_pod.capabilities,
        )
        self.assertItemsEqual([], discovered_pod.machines)
        self.assertItemsEqual([], discovered_pod.tags)
        self.assertItemsEqual([], discovered_pod.storage_pools)

    @inlineCallbacks
    def test__get_discovered_pod_storage_pool(self):
        driver = lxd_module.LXDPodDriver()
        mock_storage_pool = Mock()
        mock_storage_pool.name = factory.make_name("pool")
        mock_storage_pool.driver = "dir"
        mock_storage_pool.config = {
            "size": "61203283968",
            "source": "/home/chb/mnt/l2/disks/default.img",
            "volume.size": "0",
            "zfs.pool_name": "default",
        }
        mock_resources = Mock()
        mock_resources.space = {"used": 207111192576, "total": 306027577344}
        mock_storage_pool.resources.get.return_value = mock_resources
        discovered_pod_storage_pool = yield driver.get_discovered_pod_storage_pool(
            mock_storage_pool
        )

        self.assertEquals(
            mock_storage_pool.name, discovered_pod_storage_pool.id
        )
        self.assertEquals(
            mock_storage_pool.name, discovered_pod_storage_pool.name
        )
        self.assertEquals(
            mock_storage_pool.config["source"],
            discovered_pod_storage_pool.path,
        )
        self.assertEquals(
            mock_storage_pool.driver, discovered_pod_storage_pool.type
        )
        self.assertEquals(
            mock_resources.space["total"], discovered_pod_storage_pool.storage
        )

    @inlineCallbacks
    def test__get_discovered_machine(self):
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        mock_machine = Mock()
        mock_machine.name = factory.make_name("machine")
        mock_machine.architecture = "x86_64"
        expanded_config = {
            "limits.cpu": "2",
            "limits.memory": "1024MiB",
            "volatile.eth0.hwaddr": "00:16:3e:78:be:04",
            "volatile.eth1.hwaddr": "00:16:3e:f9:fc:cb",
        }
        expanded_devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": "lxdbr0",
                "type": "nic",
            },
            "eth1": {
                "name": "eth1",
                "nictype": "bridged",
                "parent": "virbr1",
                "type": "nic",
            },
            "root": {
                "path": "/",
                "pool": "default",
                "type": "disk",
                "size": "20GB",
            },
        }
        mock_machine.expanded_config = expanded_config
        mock_machine.expanded_devices = expanded_devices
        mock_machine.status_code = 102
        mock_storage_pool = Mock()
        mock_storage_pool.name = "default"
        mock_storage_pool_resources = Mock()
        mock_storage_pool_resources.space = {
            "used": 207111192576,
            "total": 306027577344,
        }
        mock_storage_pool.resources.get.return_value = (
            mock_storage_pool_resources
        )
        mock_machine.storage_pools.get.return_value = mock_storage_pool
        discovered_machine = yield driver.get_discovered_machine(
            client, mock_machine, [mock_storage_pool]
        )

        self.assertEquals(mock_machine.name, discovered_machine.hostname)

        self.assertEquals(
            kernel_to_debian_architecture(mock_machine.architecture),
            discovered_machine.architecture,
        )
        self.assertEquals(
            lxd_module.LXD_VM_POWER_STATE[mock_machine.status_code],
            discovered_machine.power_state,
        )
        self.assertEquals(2, discovered_machine.cores)
        self.assertEquals(1024, discovered_machine.memory)
        self.assertEquals(
            mock_machine.name,
            discovered_machine.power_parameters["instance_name"],
        )
        self.assertThat(
            discovered_machine.block_devices[0],
            MatchesStructure.byEquality(
                model="QEMU HARDDISK",
                serial="lxd_root",
                id_path="/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_root",
                size=20 * 1000 ** 3,
                block_size=512,
                tags=[],
                type="physical",
                storage_pool=expanded_devices["root"]["pool"],
                iscsi_target=None,
            ),
        )
        self.assertThat(
            discovered_machine.interfaces[0],
            MatchesStructure.byEquality(
                mac_address=expanded_config["volatile.eth0.hwaddr"],
                vid=0,
                tags=[],
                boot=True,
                attach_type=expanded_devices["eth0"]["nictype"],
                attach_name="eth0",
            ),
        )
        self.assertThat(
            discovered_machine.interfaces[1],
            MatchesStructure.byEquality(
                mac_address=expanded_config["volatile.eth1.hwaddr"],
                vid=0,
                tags=[],
                boot=False,
                attach_type=expanded_devices["eth1"]["nictype"],
                attach_name="eth1",
            ),
        )
        self.assertItemsEqual([], discovered_machine.tags)

    @inlineCallbacks
    def test_get_discovered_machine_sets_power_state_to_unknown_for_unknown(
        self
    ):
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        mock_machine = Mock()
        mock_machine.name = factory.make_name("machine")
        mock_machine.architecture = "x86_64"
        expanded_config = {
            "limits.cpu": "2",
            "limits.memory": "1024",
            "volatile.eth0.hwaddr": "00:16:3e:78:be:04",
            "volatile.eth1.hwaddr": "00:16:3e:f9:fc:cb",
        }
        expanded_devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": "lxdbr0",
                "type": "nic",
            },
            "eth1": {
                "name": "eth1",
                "nictype": "bridged",
                "parent": "virbr1",
                "type": "nic",
            },
            "root": {"path": "/", "pool": "default", "type": "disk"},
        }
        mock_machine.expanded_config = expanded_config
        mock_machine.expanded_devices = expanded_devices
        mock_machine.status_code = 100
        mock_storage_pool = Mock()
        mock_storage_pool.name = "default"
        mock_storage_pool_resources = Mock()
        mock_storage_pool_resources.space = {
            "used": 207111192576,
            "total": 306027577344,
        }
        mock_storage_pool.resources.get.return_value = (
            mock_storage_pool_resources
        )
        mock_machine.storage_pools.get.return_value = mock_storage_pool
        discovered_machine = yield driver.get_discovered_machine(
            client, mock_machine, [mock_storage_pool]
        )

        self.assertEquals("unknown", discovered_machine.power_state)

    @inlineCallbacks
    def test__get_commissioning_data(self):
        driver = lxd_module.LXDPodDriver()
        context = self.make_parameters_context()
        Client = self.patch(lxd_module, "Client")
        client = Client.return_value
        client.resources = {
            factory.make_name("rkey"): factory.make_name("rvalue")
        }
        client.host_info = {
            factory.make_name("hkey"): factory.make_name("hvalue")
        }
        commissioning_data = yield driver.get_commissioning_data(1, context)
        self.assertDictEqual(
            {
                LXD_OUTPUT_NAME: {
                    **client.host_info,
                    "resources": client.resources,
                }
            },
            commissioning_data,
        )

    def test_get_usable_storage_pool(self):
        driver = lxd_module.LXDPodDriver()
        pools = [
            Mock(
                **{
                    "resources.get.return_value": Mock(
                        space={"total": 2 ** i * 2048, "used": 2 * i * 1500}
                    )
                }
            )
            for i in range(3)
        ]
        # Override name attribute on Mock and calculate the available
        for pool in pools:
            type(pool).name = PropertyMock(
                return_value=factory.make_name("pool_name")
            )
        disk = RequestedMachineBlockDevice(
            size=2048,  # Only the first pool will have this availability.
            tags=[],
        )
        self.assertEqual(
            pools[0].name, driver.get_usable_storage_pool(disk, pools)
        )

    def test_get_usable_storage_pool_filters_on_disk_tags(self):
        driver = lxd_module.LXDPodDriver()
        pools = [
            Mock(
                **{
                    "resources.get.return_value": Mock(
                        space={"total": 2 ** i * 2048, "used": 2 * i * 1500}
                    )
                }
            )
            for i in range(3)
        ]
        # Override name attribute on Mock and calculate the available
        for pool in pools:
            type(pool).name = PropertyMock(
                return_value=factory.make_name("pool_name")
            )
        selected_pool = pools[1]
        disk = RequestedMachineBlockDevice(
            size=1024, tags=[selected_pool.name]
        )
        self.assertEqual(
            pools[1].name, driver.get_usable_storage_pool(disk, pools)
        )

    def test_get_usable_storage_pool_filters_on_disk_tags_raises_invalid(self):
        driver = lxd_module.LXDPodDriver()
        pools = [
            Mock(
                **{
                    "resources.get.return_value": Mock(
                        space={"total": 2 ** i * 2048, "used": 2 * i * 1500}
                    )
                }
            )
            for i in range(3)
        ]
        # Override name attribute on Mock and calculate the available
        for pool in pools:
            type(pool).name = PropertyMock(
                return_value=factory.make_name("pool_name")
            )
        selected_pool = pools[1]
        disk = RequestedMachineBlockDevice(
            size=2048, tags=[selected_pool.name]
        )
        self.assertRaises(
            PodInvalidResources, driver.get_usable_storage_pool, disk, pools
        )

    def test_get_usable_storage_pool_filters_on_default_pool_name(self):
        driver = lxd_module.LXDPodDriver()
        pools = [
            Mock(
                **{
                    "resources.get.return_value": Mock(
                        space={"total": 2 ** i * 2048, "used": 2 * i * 1500}
                    )
                }
            )
            for i in range(3)
        ]
        # Override name attribute on Mock and calculate the available
        for pool in pools:
            type(pool).name = PropertyMock(
                return_value=factory.make_name("pool_name")
            )
        disk = RequestedMachineBlockDevice(size=2048, tags=[])
        self.assertEqual(
            pools[0].name,
            driver.get_usable_storage_pool(disk, pools, pools[0].name),
        )

    def test_get_usable_storage_pool_filters_on_default_pool_name_raises_invalid(
        self
    ):
        driver = lxd_module.LXDPodDriver()
        pools = [
            Mock(
                **{
                    "resources.get.return_value": Mock(
                        space={"total": 2 ** i * 2048, "used": 2 * i * 1500}
                    )
                }
            )
            for i in range(3)
        ]
        # Override name attribute on Mock and calculate the available
        for pool in pools:
            type(pool).name = PropertyMock(
                return_value=factory.make_name("pool_name")
            )
        disk = RequestedMachineBlockDevice(size=2048 + 1, tags=[])
        self.assertRaises(
            PodInvalidResources,
            driver.get_usable_storage_pool,
            disk,
            pools,
            pools[0].name,
        )

    @inlineCallbacks
    def test_compose_errors_if_not_default_or_maas_profile(self):
        pod_id = factory.make_name("pod_id")
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(driver, "get_client")
        client = Client.return_value
        client.profiles.get.side_effect = [
            lxd_module.NotFound("Error"),
            lxd_module.NotFound("Error"),
        ]
        error_msg = (
            f"Pod {pod_id}: MAAS needs LXD to have either a 'maas' "
            "profile or a 'default' profile, defined."
        )
        with ExpectedException(lxd_module.LXDPodError, error_msg):
            yield driver.compose(pod_id, {}, None)

    @inlineCallbacks
    def test_compose_no_interface_constraints(self):
        pod_id = factory.make_name("pod_id")
        context = self.make_parameters_context()
        request = make_requested_machine()
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(driver, "get_client")
        client = Client.return_value
        mock_profile = Mock()
        mock_profile.name = random.choice(["maas", "default"])
        profile_devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": "lxdbr0",
                "type": "nic",
            },
            "eth1": {
                "boot.priority": "1",
                "name": "eth1",
                "nictype": "bridged",
                "parent": "virbr1",
                "type": "nic",
            },
            "root": {
                "boot.priority": "0",
                "path": "/",
                "pool": "default",
                "type": "disk",
                "size": "20GB",
            },
        }
        mock_profile.devices = profile_devices
        client.profiles.get.return_value = mock_profile
        mock_storage_pools = Mock()
        client.storage_pools.all.return_value = mock_storage_pools
        mock_get_usable_storage_pool = self.patch(
            driver, "get_usable_storage_pool"
        )
        usable_pool = factory.make_name("pool")
        mock_get_usable_storage_pool.return_value = usable_pool
        mock_get_best_nic_from_profile = self.patch(
            driver, "get_best_nic_from_profile"
        )
        mock_get_best_nic_from_profile.return_value = (
            "eth1",
            profile_devices["eth1"],
        )
        mock_machine = Mock()
        client.virtual_machines.create.return_value = mock_machine
        mock_get_discovered_machine = self.patch(
            driver, "get_discovered_machine"
        )
        mock_get_discovered_machine.return_value = sentinel.discovered_machine
        definition = {
            "name": request.hostname,
            "architecture": debian_to_kernel_architecture(
                request.architecture
            ),
            "config": {
                "limits.cpu": str(request.cores),
                "limits.memory": str(request.memory * 1024 ** 2),
                "security.secureboot": "false",
            },
            "profiles": [mock_profile.name],
            "source": {"type": "none"},
            "devices": {
                "root": {
                    "path": "/",
                    "type": "disk",
                    "pool": usable_pool,
                    "size": str(request.block_devices[0].size),
                    "boot.priority": "0",
                },
                "eth1": profile_devices["eth1"],
                "eth0": {"type": "none"},
            },
        }

        discovered_machine, empty_hints = yield driver.compose(
            pod_id, context, request
        )
        self.assertThat(
            client.virtual_machines.create,
            MockCalledOnceWith(definition, wait=True),
        )
        self.assertEquals(sentinel.discovered_machine, discovered_machine)
        self.assertThat(
            empty_hints,
            MatchesAll(
                IsInstance(DiscoveredPodHints),
                MatchesStructure(
                    cores=Equals(-1),
                    cpu_speed=Equals(-1),
                    memory=Equals(-1),
                    local_storage=Equals(-1),
                    local_disks=Equals(-1),
                    iscsi_storage=Equals(-1),
                ),
            ),
        )

    @inlineCallbacks
    def test_compose_multiple_interface_constraints(self):
        pod_id = factory.make_name("pod_id")
        context = self.make_parameters_context()
        request = make_requested_machine()
        request.interfaces = [
            RequestedMachineInterface(
                ifname=factory.make_name("ifname"),
                attach_name=factory.make_name("bridge_name"),
                attach_type="bridge",
                attach_options=None,
            )
            for _ in range(3)
        ]
        # LXD uses 'bridged' while MAAS uses 'bridge' so convert
        # the nictype as this is what we expect from LXDPodDriver.compose.
        expected_interfaces = [
            {
                "name": request.interfaces[i].ifname,
                "parent": request.interfaces[i].attach_name,
                "nictype": "bridged",
                "type": "nic",
            }
            for i in range(3)
        ]
        expected_interfaces[0]["boot.priority"] = "1"
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(driver, "get_client")
        client = Client.return_value
        mock_profile = Mock()
        mock_profile.name = random.choice(["maas", "default"])
        profile_devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": "lxdbr0",
                "type": "nic",
            },
            "eth1": {
                "boot.priority": "1",
                "name": "eth1",
                "nictype": "bridged",
                "parent": "virbr1",
                "type": "nic",
            },
            "root": {
                "boot.priority": "0",
                "path": "/",
                "pool": "default",
                "type": "disk",
                "size": "20GB",
            },
        }
        mock_profile.devices = profile_devices
        client.profiles.get.return_value = mock_profile
        mock_storage_pools = Mock()
        client.storage_pools.all.return_value = mock_storage_pools
        mock_get_usable_storage_pool = self.patch(
            driver, "get_usable_storage_pool"
        )
        usable_pool = factory.make_name("pool")
        mock_get_usable_storage_pool.return_value = usable_pool
        mock_get_best_nic_from_profile = self.patch(
            driver, "get_best_nic_from_profile"
        )
        mock_get_best_nic_from_profile.return_value = (
            "eth1",
            profile_devices["eth1"],
        )
        mock_machine = Mock()
        client.virtual_machines.create.return_value = mock_machine
        mock_get_discovered_machine = self.patch(
            driver, "get_discovered_machine"
        )
        mock_get_discovered_machine.return_value = sentinel.discovered_machine
        definition = {
            "name": request.hostname,
            "architecture": debian_to_kernel_architecture(
                request.architecture
            ),
            "config": {
                "limits.cpu": str(request.cores),
                "limits.memory": str(request.memory * 1024 ** 2),
                "security.secureboot": "false",
            },
            "profiles": [mock_profile.name],
            "source": {"type": "none"},
            "devices": {
                "root": {
                    "path": "/",
                    "type": "disk",
                    "pool": usable_pool,
                    "size": str(request.block_devices[0].size),
                    "boot.priority": "0",
                },
                expected_interfaces[0]["name"]: expected_interfaces[0],
                expected_interfaces[1]["name"]: expected_interfaces[1],
                expected_interfaces[2]["name"]: expected_interfaces[2],
                "eth1": {"type": "none"},
                "eth0": {"type": "none"},
            },
        }

        discovered_machine, empty_hints = yield driver.compose(
            pod_id, context, request
        )
        self.assertThat(
            client.virtual_machines.create,
            MockCalledOnceWith(definition, wait=True),
        )
        self.assertEquals(sentinel.discovered_machine, discovered_machine)
        self.assertThat(
            empty_hints,
            MatchesAll(
                IsInstance(DiscoveredPodHints),
                MatchesStructure(
                    cores=Equals(-1),
                    cpu_speed=Equals(-1),
                    memory=Equals(-1),
                    local_storage=Equals(-1),
                    local_disks=Equals(-1),
                    iscsi_storage=Equals(-1),
                ),
            ),
        )

    @inlineCallbacks
    def test_decompose(self):
        pod_id = factory.make_name("pod_id")
        context = self.make_parameters_context()
        driver = lxd_module.LXDPodDriver()
        Client = self.patch(driver, "get_client")
        client = Client.return_value
        mock_machine = Mock()
        client.virtual_machines.get.return_value = mock_machine
        empty_hints = yield driver.decompose(pod_id, context)

        self.assertThat(mock_machine.stop, MockCalledOnceWith())
        self.assertThat(mock_machine.delete, MockCalledOnceWith(wait=True))
        self.assertThat(
            empty_hints,
            MatchesAll(
                IsInstance(DiscoveredPodHints),
                MatchesStructure(
                    cores=Equals(-1),
                    cpu_speed=Equals(-1),
                    memory=Equals(-1),
                    local_storage=Equals(-1),
                    local_disks=Equals(-1),
                    iscsi_storage=Equals(-1),
                ),
            ),
        )
