# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maasserver.triggers`."""

__all__ = []

from contextlib import closing

from django.db import connection
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.triggers import (
    register_all_triggers,
    register_procedure,
    register_trigger,
    render_notification_procedure,
)
from maasserver.utils.orm import psql_array


class TestTriggers(MAASServerTestCase):

    def test_register_trigger_doesnt_create_trigger_if_already_exists(self):
        NODE_CREATE_PROCEDURE = render_notification_procedure(
            'node_create_notify', 'node_create', 'NEW.system_id')
        register_procedure(NODE_CREATE_PROCEDURE)
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                "DROP TRIGGER IF EXISTS maasserver_node_node_create_notify ON "
                "maasserver_node;"
                "CREATE TRIGGER maasserver_node_node_create_notify "
                "AFTER INSERT ON maasserver_node "
                "FOR EACH ROW EXECUTE PROCEDURE node_create_notify();")

        # Will raise an OperationError if trigger already exists.
        register_trigger("maasserver_node", "node_create_notify", "insert")

    def test_register_trigger_creates_missing_trigger(self):
        NODE_CREATE_PROCEDURE = render_notification_procedure(
            'node_create_notify', 'node_create', 'NEW.system_id')
        register_procedure(NODE_CREATE_PROCEDURE)
        register_trigger("maasserver_node", "node_create_notify", "insert")

        with closing(connection.cursor()) as cursor:
            cursor.execute(
                "SELECT * FROM pg_trigger WHERE "
                "tgname = 'maasserver_node_node_create_notify'")
            triggers = cursor.fetchall()

        self.assertEqual(1, len(triggers), "Trigger was not created.")

    def test_register_all_triggers(self):
        register_all_triggers()
        triggers = [
            "maasserver_node_machine_create_notify",
            "maasserver_node_machine_update_notify",
            "maasserver_node_machine_delete_notify",
            "maasserver_node_device_create_notify",
            "maasserver_node_device_update_notify",
            "maasserver_node_device_delete_notify",
            "maasserver_zone_zone_create_notify",
            "maasserver_zone_zone_update_notify",
            "maasserver_zone_zone_delete_notify",
            "maasserver_tag_tag_create_notify",
            "maasserver_tag_tag_update_notify",
            "maasserver_tag_tag_delete_notify",
            "maasserver_node_tags_machine_device_tag_link_notify",
            "maasserver_node_tags_machine_device_tag_unlink_notify",
            "maasserver_tag_tag_update_machine_device_notify",
            "auth_user_user_create_notify",
            "auth_user_user_update_notify",
            "auth_user_user_delete_notify",
            "maasserver_event_event_create_notify",
            "maasserver_event_event_update_notify",
            "maasserver_event_event_delete_notify",
            "maasserver_event_event_create_machine_device_notify",
            "maasserver_interface_ip_addresses_nd_sipaddress_link_notify",
            "maasserver_interface_ip_addresses_nd_sipaddress_unlink_notify",
            "metadataserver_noderesult_nd_noderesult_link_notify",
            "metadataserver_noderesult_nd_noderesult_unlink_notify",
            "maasserver_interface_nd_interface_link_notify",
            "maasserver_interface_nd_interface_unlink_notify",
            "maasserver_interface_nd_interface_update_notify",
            "maasserver_blockdevice_nd_blockdevice_link_notify",
            "maasserver_blockdevice_nd_blockdevice_unlink_notify",
            "maasserver_physicalblockdevice_nd_physblockdevice_update_notify",
            "maasserver_virtualblockdevice_nd_virtblockdevice_update_notify",
            "maasserver_sshkey_user_sshkey_link_notify",
            "maasserver_sshkey_user_sshkey_unlink_notify",
            "maasserver_sslkey_user_sslkey_link_notify",
            "maasserver_sslkey_user_sslkey_unlink_notify",
            "maasserver_fabric_fabric_create_notify",
            "maasserver_fabric_fabric_update_notify",
            "maasserver_fabric_fabric_delete_notify",
            "maasserver_vlan_vlan_create_notify",
            "maasserver_vlan_vlan_update_notify",
            "maasserver_vlan_vlan_delete_notify",
            "maasserver_subnet_subnet_create_notify",
            "maasserver_subnet_subnet_update_notify",
            "maasserver_subnet_subnet_delete_notify",
            "maasserver_space_space_create_notify",
            "maasserver_space_space_update_notify",
            "maasserver_space_space_delete_notify",
            "maasserver_subnet_subnet_machine_update_notify",
            "maasserver_fabric_fabric_machine_update_notify",
            "maasserver_space_space_machine_update_notify",
            "maasserver_vlan_vlan_machine_update_notify",
            "maasserver_staticipaddress_ipaddress_machine_update_notify",
            "maasserver_staticipaddress_ipaddress_subnet_update_notify",
            ]
        sql, args = psql_array(triggers, sql_type="text")
        with closing(connection.cursor()) as cursor:
            cursor.execute(
                "SELECT tgname::text FROM pg_trigger WHERE "
                "tgname::text = ANY(%s) "
                "OR tgname::text SIMILAR TO 'maasserver.*'" % sql, args)
            db_triggers = cursor.fetchall()

        # Note: if this test fails, a trigger may have been added, but not
        # added to the list of expected triggers.
        triggers_found = [trigger[0] for trigger in db_triggers]
        self.assertEqual(
            len(triggers), len(db_triggers),
            "Missing %s triggers in the database. Triggers found: %s" % (
                len(triggers) - len(db_triggers), triggers_found))

        self.assertItemsEqual(
            triggers, triggers_found,
            "Missing triggers in the database. Triggers found: %s" % (
                triggers_found))
