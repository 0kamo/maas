# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maasserver.websockets.handlers.general`"""

__all__ = []

from operator import itemgetter

from distro_info import UbuntuDistroInfo
from maasserver.enum import (
    BOND_LACP_RATE_CHOICES,
    BOND_MODE_CHOICES,
    BOND_XMIT_HASH_POLICY_CHOICES,
    BOOT_RESOURCE_TYPE,
)
from maasserver.models import BootSourceCache
from maasserver.models.config import Config
from maasserver.node_action import ACTIONS_DICT
from maasserver.testing.factory import factory
from maasserver.testing.osystems import make_osystem_with_releases
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.orm import post_commit_hooks
from maasserver.websockets.handlers import general
from maasserver.websockets.handlers.general import GeneralHandler
from mock import sentinel
import petname


class TestGeneralHandler(MAASServerTestCase):

    def dehydrate_actions(self, actions):
        return sorted([
            {
                "name": name,
                "title": action.display,
                "sentence": action.display_sentence,
            }
            for name, action in actions.items()
            ], key=itemgetter("name"))

    def test_architectures(self):
        arches = [
            "%s/%s" % (factory.make_name("arch"), factory.make_name("subarch"))
            for _ in range(3)
            ]
        for arch in arches:
            factory.make_usable_boot_resource(architecture=arch)
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEqual(sorted(arches), handler.architectures({}))

    def test_hwe_kernels(self):
        ubuntu_releases = UbuntuDistroInfo()
        expected_output = []
        # Stub out the post commit tasks otherwise the test fails due to
        # unrun post-commit tasks at the end of the test.
        self.patch(BootSourceCache, "post_commit_do")
        # Start with the first release MAAS supported. We do this
        # because the lookup between hwe- kernel and release can fail
        # when multiple releases start with the same letter. For
        # example both warty(4.10) and wily(15.10) will have an hwe-w
        # kernel. Because of this the mapping between kernel and
        # release will pick the release which was downloaded
        # first. Since precise no release has used the same first
        # letter so we do not have this problem with supported
        # releases.
        for release in ubuntu_releases.all[
                ubuntu_releases.all.index('precise'):]:
            kernel = 'hwe-' + release[0]
            arch = factory.make_name('arch')
            architecture = "%s/%s" % (arch, kernel)
            factory.make_usable_boot_resource(
                name="ubuntu/" + release,
                extra={'subarches': kernel},
                architecture=architecture,
                rtype=BOOT_RESOURCE_TYPE.SYNCED)
            # Force run the post commit tasks as we make new boot sources
            with post_commit_hooks:
                factory.make_BootSourceCache(
                    os="ubuntu",
                    arch=arch,
                    subarch=kernel,
                    release=release)
            expected_output.append((kernel, '%s (%s)' % (release, kernel)))
        handler = GeneralHandler(factory.make_User(), {})
        self.assertItemsEqual(
            sorted(expected_output, key=lambda choice: choice[0]),
            sorted(handler.hwe_kernels({}), key=lambda choice: choice[0]))

    def test_osinfo(self):
        handler = GeneralHandler(factory.make_User(), {})
        osystem = make_osystem_with_releases(self)
        releases = [
            ("%s/%s" % (osystem["name"], release["name"]), release["title"])
            for release in osystem["releases"]
        ]
        expected_osinfo = {
            "osystems": [
                (osystem["name"], osystem["title"]),
            ],
            "releases": releases,
            "kernels": None,
            "default_osystem": Config.objects.get_config("default_osystem"),
            "default_release": Config.objects.get_config(
                "default_distro_series"),
        }
        self.assertItemsEqual(expected_osinfo, handler.osinfo({}))

    def test_machine_actions_for_admin(self):
        handler = GeneralHandler(factory.make_admin(), {})
        actions_expected = self.dehydrate_actions(ACTIONS_DICT)
        self.assertItemsEqual(actions_expected, handler.machine_actions({}))

    def test_machine_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertItemsEqual(
            ['release', 'mark-broken', 'on', 'deploy', 'mark-fixed',
             'commission', 'abort', 'acquire', 'off'],
            [action['name'] for action in handler.machine_actions({})])

    def test_device_actions_for_admin(self):
        handler = GeneralHandler(factory.make_admin(), {})
        self.assertItemsEqual(
            ['set-zone', 'delete'],
            [action['name'] for action in handler.device_actions({})])

    def test_device_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertItemsEqual([], handler.device_actions({}))

    def test_region_controller_actions_for_admin(self):
        handler = GeneralHandler(factory.make_admin(), {})
        self.assertItemsEqual(
            ['set-zone', 'delete'],
            [action['name']
             for action in handler.region_controller_actions({})])

    def test_region_controller_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertItemsEqual([], handler.region_controller_actions({}))

    def test_rack_controller_actions_for_admin(self):
        handler = GeneralHandler(factory.make_admin(), {})
        self.assertItemsEqual(
            ['delete', 'off', 'on', 'set-zone'],
            [action['name'] for action in handler.rack_controller_actions({})])

    def test_rack_controller_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertItemsEqual([], handler.rack_controller_actions({}))

    def test_region_and_rack_controller_actions_for_admin(self):
        handler = GeneralHandler(factory.make_admin(), {})
        self.assertItemsEqual(
            ['set-zone', 'delete'],
            [action['name']
             for action in handler.region_and_rack_controller_actions({})])

    def test_region_and_rack_controller_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertItemsEqual(
            [], handler.region_and_rack_controller_actions({}))

    def test_random_hostname_checks_hostname_existence(self):
        existing_node = factory.make_Node(hostname="hostname")
        hostnames = [existing_node.hostname, "new-hostname"]
        self.patch(
            petname, "Generate").side_effect = hostnames
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEqual("new-hostname", handler.random_hostname({}))

    def test_bond_options(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEqual({
            "modes": BOND_MODE_CHOICES,
            "lacp_rates": BOND_LACP_RATE_CHOICES,
            "xmit_hash_policies": BOND_XMIT_HASH_POLICY_CHOICES,
            }, handler.bond_options({}))

    def test_version(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.patch_autospec(
            general, "get_maas_version_ui").return_value = sentinel.version
        self.assertEqual(sentinel.version, handler.version({}))

    def test_power_types(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.patch_autospec(
            general,
            "get_all_power_types_from_clusters").return_value = sentinel.types
        self.assertEqual(sentinel.types, handler.power_types({}))
