# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Configuration items."""

__all__ = [
    'Config',
    ]


from collections import defaultdict
import copy
from socket import gethostname

from django.db.models import (
    CharField,
    Manager,
    Model,
)
from django.db.models.signals import post_save
from maasserver import DefaultMeta
from maasserver.fields import JSONObjectField
from provisioningserver.drivers.osystem.ubuntu import UbuntuOS


DEFAULT_OS = UbuntuOS()
DNSSEC_VALIDATION_CHOICES = [
    ("auto", "Automatic (use default root key)"),
    ("yes", "Yes (manually configured root key)"),
    ("no", "No (Disable DNSSEC; useful when upstream DNS is misconfigured)")
]


def get_default_config():
    """
    :return: A dictionary mapping default settings keys to default values.
    """
    return {
        # Ubuntu section configuration.
        'main_archive': 'http://archive.ubuntu.com/ubuntu',
        'ports_archive': 'http://ports.ubuntu.com/ubuntu-ports',
        'commissioning_osystem': DEFAULT_OS.name,
        'commissioning_distro_series':
        DEFAULT_OS.get_default_commissioning_release(),
        'default_dns_ttl': 30,
        'default_min_hwe_kernel': '',
        'default_storage_layout': 'flat',
        # Network section configuration.
        'maas_name': gethostname(),
        'default_osystem': DEFAULT_OS.name,
        'default_distro_series': DEFAULT_OS.get_default_release(),
        'enable_http_proxy': True,
        'http_proxy': None,
        'upstream_dns': None,
        'dnssec_validation': "auto",
        'ntp_server': 'ntp.ubuntu.com',
        # RPC configuration.
        'rpc_region_certificate': None,
        'rpc_shared_secret': None,
        # Images.
        'boot_images_auto_import': True,
        # Third Party
        'enable_third_party_drivers': True,
        'enable_disk_erasing_on_release': False,
        # Curtin.
        'curtin_verbose': False,
        # # /settings
        }


# Default values for config options.
DEFAULT_CONFIG = get_default_config()


class ConfigManager(Manager):
    """Manager for Config model class.

    Don't import or instantiate this directly; access as `Config.objects.
    """

    def __init__(self):
        super(ConfigManager, self).__init__()
        self._config_changed_connections = defaultdict(set)

    def get_config(self, name, default=None):
        """Return the config value corresponding to the given config name.
        Return None or the provided default if the config value does not
        exist.

        :param name: The name of the config item.
        :type name: unicode
        :param name: The optional default value to return if no such config
            item exists.
        :type name: object
        :return: A config value.
        :raises: Config.MultipleObjectsReturned
        """
        try:
            return self.get(name=name).value
        except Config.DoesNotExist:
            return copy.deepcopy(DEFAULT_CONFIG.get(name, default))
        except Config.MultipleObjectsReturned as error:
            raise Config.MultipleObjectsReturned("%s (%s)" (error, name))

    def set_config(self, name, value):
        """Set or overwrite a config value.

        :param name: The name of the config item to set.
        :type name: unicode
        :param value: The value of the config item to set.
        :type value: Any jsonizable object
        """
        config, freshly_created = self.get_or_create(
            name=name, defaults=dict(value=value))
        if not freshly_created:
            config.value = value
            config.save()

    def config_changed_connect(self, config_name, method):
        """Connect a method to Django's 'update' signal for given config name.

        :param config_name: The name of the config item to track.
        :type config_name: unicode
        :param method: The method to be called.
        :type method: callable

        The provided callable should follow Django's convention.  E.g::

          >>> def callable(sender, instance, created, **kwargs):
          ...     pass

          >>> Config.objects.config_changed_connect('config_name', callable)

        """
        self._config_changed_connections[config_name].add(method)

    def config_changed_disconnect(self, config_name, method):
        """Disconnect from Django's 'update' signal for given config name.

        :param config_name: The name of the config item.
        :type config_name: unicode
        :param method: The method to be removed.
        :type method: callable
        """
        self._config_changed_connections[config_name].discard(method)

    def _config_changed(self, sender, instance, created, **kwargs):
        for connection in self._config_changed_connections[instance.name]:
            connection(sender, instance, created, **kwargs)


class Config(Model):
    """Configuration settings item.

    :ivar name: The name of the configuration option.
    :type name: unicode
    :ivar value: The configuration value.
    :type value: Any pickleable python object.
    """

    class Meta(DefaultMeta):
        """Needed for South to recognize this model."""

    name = CharField(max_length=255, unique=True)
    value = JSONObjectField(null=True)

    objects = ConfigManager()

    def __str__(self):
        return "%s: %s" % (self.name, self.value)


# Connect config manager's _config_changed to Config's post-save signal.
post_save.connect(Config.objects._config_changed, sender=Config)
