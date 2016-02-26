# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the VLAN model."""

__all__ = []

import random
import re

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from maasserver.enum import INTERFACE_TYPE
from maasserver.models.interface import (
    PhysicalInterface,
    VLANInterface,
)
from maasserver.models.vlan import VLAN
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.testing.testcase import MAASServerTestCase
from testtools.matchers import (
    Equals,
    Is,
    MatchesStructure,
)
from testtools.testcase import ExpectedException


class TestVLANManager(MAASServerTestCase):

    def test__default_specifier_matches_vid(self):
        # Note: this is for backward compatibility with the previous iteration
        # of constraints, which used vlan:<number> to mean VID, not represent
        # a database ID.
        factory.make_VLAN()
        vlan = factory.make_VLAN()
        factory.make_VLAN()
        vid = vlan.vid
        self.assertItemsEqual(
            VLAN.objects.filter_by_specifiers('%s' % vid),
            [vlan]
        )

    def test__default_specifier_matches_name(self):
        factory.make_VLAN()
        vlan = factory.make_VLAN(name='infinite-improbability')
        factory.make_VLAN()
        self.assertItemsEqual(
            VLAN.objects.filter_by_specifiers('infinite-improbability'),
            [vlan]
        )

    def test__name_specifier_matches_name(self):
        factory.make_VLAN()
        vlan = factory.make_VLAN(name='infinite-improbability')
        factory.make_VLAN()
        self.assertItemsEqual(
            VLAN.objects.filter_by_specifiers('name:infinite-improbability'),
            [vlan]
        )

    def test__vid_specifier_matches_vid(self):
        factory.make_VLAN()
        vlan = factory.make_VLAN()
        vid = vlan.vid
        factory.make_VLAN()
        self.assertItemsEqual(
            VLAN.objects.filter_by_specifiers('vid:%d' % vid),
            [vlan]
        )

    def test__class_specifier_matches_attached_subnet(self):
        factory.make_VLAN()
        vlan = factory.make_VLAN()
        subnet = factory.make_Subnet(vlan=vlan)
        factory.make_VLAN()
        self.assertItemsEqual(
            VLAN.objects.filter_by_specifiers('subnet:%s' % subnet.id),
            [vlan]
        )

    def test__class_specifier_matches_attached_fabric(self):
        factory.make_Fabric()
        fabric = factory.make_Fabric(name='rack42')
        factory.make_VLAN()
        vlan = factory.make_VLAN(fabric=fabric)
        factory.make_VLAN()
        self.assertItemsEqual(
            VLAN.objects.filter_by_specifiers(
                'fabric:%s,vid:%d' % (fabric.name, vlan.vid)), [vlan])


class TestVLAN(MAASServerTestCase):

    def test_get_name_for_default_vlan_is_untagged(self):
        fabric = factory.make_Fabric()
        self.assertEqual("untagged", fabric.get_default_vlan().get_name())

    def test_get_name_for_set_name(self):
        name = factory.make_name('name')
        vlan = factory.make_VLAN(name=name)
        self.assertEqual(name, vlan.get_name())

    def test_creates_vlan(self):
        name = factory.make_name('name')
        vid = random.randint(3, 55)
        fabric = factory.make_Fabric()
        vlan = VLAN(vid=vid, name=name, fabric=fabric)
        vlan.save()
        self.assertThat(vlan, MatchesStructure.byEquality(
            vid=vid, name=name))

    def test_is_fabric_default_detects_default_vlan(self):
        fabric = factory.make_Fabric()
        factory.make_VLAN(fabric=fabric)
        vlan = fabric.vlan_set.all().order_by('id').first()
        self.assertTrue(vlan.is_fabric_default())

    def test_is_fabric_default_detects_non_default_vlan(self):
        vlan = factory.make_VLAN()
        self.assertFalse(vlan.is_fabric_default())

    def test_cant_delete_default_vlan(self):
        name = factory.make_name('name')
        fabric = factory.make_Fabric(name=name)
        with ExpectedException(ValidationError):
            fabric.get_default_vlan().delete()

    def test_manager_get_default_vlan_returns_dflt_vlan_of_dflt_fabric(self):
        factory.make_Fabric()
        vlan = VLAN.objects.get_default_vlan()
        self.assertTrue(vlan.is_fabric_default())
        self.assertTrue(vlan.fabric.is_default())

    def test_vlan_interfaces_are_deleted_when_related_vlan_is_deleted(self):
        node = factory.make_Node()
        parent = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, node=node)
        vlan = factory.make_VLAN()
        interface = factory.make_Interface(
            INTERFACE_TYPE.VLAN, vlan=vlan, parents=[parent])
        vlan.delete()
        self.assertItemsEqual(
            [], VLANInterface.objects.filter(id=interface.id))

    def test_interfaces_are_reconnected_when_vlan_is_deleted(self):
        node = factory.make_Node()
        vlan = factory.make_VLAN()
        fabric = vlan.fabric
        interface = factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL,
            node=node, vlan=vlan)
        vlan.delete()
        reconnected_interfaces = PhysicalInterface.objects.filter(
            id=interface.id)
        self.assertItemsEqual([interface], reconnected_interfaces)
        reconnected_interface = reconnected_interfaces[0]
        self.assertEqual(
            reconnected_interface.vlan, fabric.get_default_vlan())

    def test_raises_integrity_error_if_reconnecting_fails(self):
        # Here we test a corner case: we test that the DB refuses to
        # leave an interface without a VLAN in case the reconnection
        # fails when a VLAN is deleted.
        vlan = factory.make_VLAN()
        # Break 'manage_connected_interfaces'.
        self.patch(vlan, 'manage_connected_interfaces')
        factory.make_Interface(
            INTERFACE_TYPE.PHYSICAL, vlan=vlan)
        with ExpectedException(ProtectedError):
            vlan.delete()

    def test_subnets_are_reconnected_when_vlan_is_deleted(self):
        fabric = factory.make_Fabric()
        vlan = factory.make_VLAN(fabric=fabric)
        subnet = factory.make_Subnet(vlan=vlan)
        vlan.delete()
        self.assertEqual(
            reload_object(subnet).vlan, fabric.get_default_vlan())


class TestVLANConfigureDHCP(MAASServerTestCase):

    def _regex(self, string):
        """Returns an escaped regular expression for the given string, which
        will match the given string anywhere in the input to the regex.
        """
        return ".*" + re.escape(string) + ".*"

    def test__unconfigures_dhcp(self):
        primary = factory.make_RackController()
        secondary = factory.make_RackController()
        vlan = factory.make_VLAN()
        vlan.dhcp_on = True
        vlan.primary_rack = primary
        vlan.secondary_rack = secondary
        vlan.configure_dhcp([])
        self.assertThat(vlan.dhcp_on, Equals(False))
        self.assertThat(vlan.primary_rack, Is(None))
        self.assertThat(vlan.secondary_rack, Is(None))

    def test__configures_dhcp_with_one_controller(self):
        primary = factory.make_RackController()
        secondary = factory.make_RackController()
        vlan = factory.make_VLAN()
        vlan.dhcp_on = False
        vlan.primary_rack = primary
        vlan.secondary_rack = secondary
        vlan.configure_dhcp([primary])
        self.assertThat(vlan.dhcp_on, Equals(True))
        self.assertThat(vlan.primary_rack, Is(primary))
        self.assertThat(vlan.secondary_rack, Is(None))

    def test__configures_dhcp_with_two_controllers(self):
        primary = factory.make_RackController()
        secondary = factory.make_RackController()
        vlan = factory.make_VLAN()
        vlan.configure_dhcp([primary, secondary])
        self.assertThat(vlan.dhcp_on, Equals(True))
        self.assertThat(vlan.primary_rack, Is(primary))
        self.assertThat(vlan.secondary_rack, Is(secondary))

    def test__rejects_non_list(self):
        vlan = factory.make_VLAN()
        with ExpectedException(
                AssertionError, self._regex(VLAN.MUST_SPECIFY_LIST_ERROR)):
            vlan.configure_dhcp(1)

    def test__rejects_three_item_list(self):
        rack1 = factory.make_RackController()
        rack2 = factory.make_RackController()
        rack3 = factory.make_RackController()
        vlan = factory.make_VLAN()
        with ExpectedException(
                ValueError, self._regex(
                    VLAN.INVALID_NUMBER_OF_CONTROLLERS_ERROR % 3)):
            vlan.configure_dhcp([rack1, rack2, rack3])

    def test__rejects_list_with_duplicate_items(self):
        rack = factory.make_RackController()
        vlan = factory.make_VLAN()
        with ExpectedException(
                ValidationError, self._regex(VLAN.DUPLICATE_CONTROLLER_ERROR)):
            vlan.configure_dhcp([rack, rack])


class TestVLANVidValidation(MAASServerTestCase):

    scenarios = [
        ('0', {'vid': 0, 'valid': True}),
        ('12', {'vid': 12, 'valid': True}),
        ('250', {'vid': 250, 'valid': True}),
        ('3000', {'vid': 3000, 'valid': True}),
        ('4095', {'vid': 4095, 'valid': True}),
        ('-23', {'vid': -23, 'valid': False}),
        ('4096', {'vid': 4096, 'valid': False}),
        ('10000', {'vid': 10000, 'valid': False}),
    ]

    def test_validates_vid(self):
        fabric = factory.make_Fabric()
        # Update the VID of the default VLAN so that it doesn't clash with
        # the VIDs we're testing here.
        default_vlan = fabric.get_default_vlan()
        default_vlan.vid = 999
        default_vlan.save()
        name = factory.make_name('name')
        vlan = VLAN(vid=self.vid, name=name, fabric=fabric)
        if self.valid:
            # No exception.
            self.assertIsNone(vlan.save())

        else:
            with ExpectedException(ValidationError):
                vlan.save()


class VLANMTUValidationTest(MAASServerTestCase):

    scenarios = [
        ('551', {'mtu': 551, 'valid': False}),
        ('552', {'mtu': 552, 'valid': True}),
        ('65535', {'mtu': 65535, 'valid': True}),
        ('65536', {'mtu': 65536, 'valid': False}),
    ]

    def test_validates_mtu(self):
        vlan = factory.make_VLAN()
        vlan.mtu = self.mtu
        if self.valid:
            # No exception.
            self.assertIsNone(vlan.save())
        else:
            with ExpectedException(ValidationError):
                vlan.save()
