# Copyright 2016-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Base pod driver."""

__all__ = [
    "PodActionError",
    "PodAuthError",
    "PodConnError",
    "PodDriver",
    "PodDriverBase",
    "PodError",
    "PodFatalError",
    ]

from abc import abstractmethod

import attr
from provisioningserver.drivers import (
    IP_EXTRACTOR_SCHEMA,
    SETTING_PARAMETER_FIELD_SCHEMA,
)
from provisioningserver.drivers.power import (
    PowerDriver,
    PowerDriverBase,
)

# JSON schema for what a pod driver definition should look like.
JSON_POD_DRIVER_SCHEMA = {
    'title': "Pod driver setting set",
    'type': 'object',
    'properties': {
        'driver_type': {
            'type': 'string',
        },
        'name': {
            'type': 'string',
        },
        'description': {
            'type': 'string',
        },
        'fields': {
            'type': 'array',
            'items': SETTING_PARAMETER_FIELD_SCHEMA,
        },
        'ip_extractor': IP_EXTRACTOR_SCHEMA,
        'queryable': {
            'type': 'boolean',
        },
        'missing_packages': {
            'type': 'array',
            'items': {
                'type': 'string',
            },
        },
    },
    'required': [
        'driver_type', 'name', 'description', 'fields'],
}

# JSON schema for multple pod drivers.
JSON_POD_DRIVERS_SCHEMA = {
    'title': "Pod drivers parameters set",
    'type': 'array',
    'items': JSON_POD_DRIVER_SCHEMA,
}


class PodError(Exception):
    """Base error for all pod driver failure commands."""


class PodFatalError(PodError):
    """Error that is raised when the pod action should not continue to
    retry at all.

    This exception will cause the pod action to fail instantly,
    without retrying.
    """


class PodAuthError(PodFatalError):
    """Error raised when pod driver fails to authenticate to the pod.

    This exception will cause the pod action to fail instantly,
    without retrying.
    """


class PodConnError(PodError):
    """Error raised when pod driver fails to communicate to the pod."""


class PodActionError(PodError):
    """Error when actually performing an action on the pod, like `compose`
    or `discover`."""


def convert_obj(expected, optional=False):
    """Convert the given value to an object of type `expected`."""
    def convert(value):
        if optional and value is None:
            return None
        if isinstance(value, expected):
            return value
        elif isinstance(value, dict):
            return expected(**value)
        else:
            raise TypeError(
                "%r is not of type %s or dict" % (value, expected))
    return convert


def convert_list(expected):
    """Convert the given value to a list of objects of type `expected`."""
    def convert(value):
        if isinstance(value, list):
            if len(value) == 0:
                return value
            else:
                new_list = []
                for item in value:
                    if isinstance(item, expected):
                        new_list.append(item)
                    elif isinstance(item, dict):
                        new_list.append(expected(**item))
                    else:
                        raise TypeError(
                            "Item %r is not of type %s or dict" % (
                                item, expected))
                return new_list
        else:
            raise TypeError("%r is not of type list" % value)
    return convert


class Capabilities:
    """Capabilities that a pod supports."""

    # Supports the ability for machines to be composable. Driver must
    # implement the `compose` and `decompose` methods when set.
    COMPOSABLE = 'composable'

    # Supports fixed local storage. Block devices are fixed in size locally
    # and its possible to get a disk larger than requested.
    FIXED_LOCAL_STORAGE = 'fixed_local_storage'

    # Supports dynamic local storage. Block devices are dynamically created,
    # attached locally and will always be the exact requested size.
    DYNAMIC_LOCAL_STORAGE = 'dynamic_local_storage'

    # Supports built-in iscsi storage. Remote block devices can be created of
    # exact size with this pod connected storage systems.
    ISCSI_STORAGE = 'iscsi_storage'

    # Ability to over commit the cores and memory of the pod. Mainly used
    # for virtual pod.
    OVER_COMMIT = 'over_commit'


class AttrHelperMixin:
    """Mixin to add the `fromdict` and `asdict` to the classes."""

    @classmethod
    def fromdict(cls, data):
        """Convert from a dictionary."""
        return cls(**data)

    def asdict(self):
        """Convert to a dictionary."""
        return attr.asdict(self)


@attr.s
class DiscoveredMachineInterface(AttrHelperMixin):
    """Discovered machine interface."""
    mac_address = attr.ib(convert=str)
    vid = attr.ib(convert=int, default=-1)
    tags = attr.ib(convert=convert_list(str), default=[])


@attr.s
class DiscoveredMachineBlockDevice(AttrHelperMixin):
    """Discovered machine block device."""
    model = attr.ib(convert=convert_obj(str, optional=True))
    serial = attr.ib(convert=convert_obj(str, optional=True))
    size = attr.ib(convert=int)
    block_size = attr.ib(convert=int, default=512)
    tags = attr.ib(convert=convert_list(str), default=[])
    id_path = attr.ib(convert=convert_obj(str, optional=True), default=None)


@attr.s
class DiscoveredMachine(AttrHelperMixin):
    """Discovered machine."""
    architecture = attr.ib(convert=str)
    cores = attr.ib(convert=int)
    cpu_speed = attr.ib(convert=int)
    memory = attr.ib(convert=int)
    interfaces = attr.ib(convert=convert_list(DiscoveredMachineInterface))
    block_devices = attr.ib(
        convert=convert_list(DiscoveredMachineBlockDevice))
    power_state = attr.ib(convert=str, default='unknown')
    power_parameters = attr.ib(convert=convert_obj(dict), default={})
    tags = attr.ib(convert=convert_list(str), default=[])


@attr.s
class DiscoveredPodHints(AttrHelperMixin):
    """Discovered pod hints.

    Hints provide helpful information to a user trying to compose a machine.
    Limiting the maximum cores allow request on a per machine basis.
    """
    cores = attr.ib(convert=int)
    cpu_speed = attr.ib(convert=int)
    memory = attr.ib(convert=int)
    local_storage = attr.ib(convert=int)
    local_disks = attr.ib(convert=int, default=-1)


@attr.s
class DiscoveredPod(AttrHelperMixin):
    """Discovered pod information."""
    architectures = attr.ib(convert=convert_list(str))
    cores = attr.ib(convert=int)
    cpu_speed = attr.ib(convert=int)
    memory = attr.ib(convert=int)
    local_storage = attr.ib(convert=int)
    hints = attr.ib(convert=convert_obj(DiscoveredPodHints))
    local_disks = attr.ib(convert=int, default=-1)
    capabilities = attr.ib(
        convert=convert_list(str), default=[Capabilities.FIXED_LOCAL_STORAGE])
    machines = attr.ib(
        convert=convert_list(DiscoveredMachine), default=[])


@attr.s
class RequestedMachineBlockDevice(AttrHelperMixin):
    """Requested machine block device information."""
    size = attr.ib(convert=int)


@attr.s
class RequestedMachineInterface(AttrHelperMixin):
    """Requested machine interface information."""
    # Currently has no parameters.


@attr.s
class RequestedMachine(AttrHelperMixin):
    """Requested machine information."""
    architecture = attr.ib(convert=str)
    cores = attr.ib(convert=int)
    memory = attr.ib(convert=int)
    block_devices = attr.ib(convert=convert_list(RequestedMachineBlockDevice))
    interfaces = attr.ib(convert=convert_list(RequestedMachineInterface))

    # Optional fields.
    cpu_speed = attr.ib(
        convert=convert_obj(int, optional=True), default=None)

    @classmethod
    def fromdict(cls, data):
        """Convert from a dictionary."""
        return cls(**data)

    def asdict(self):
        """Convert to a dictionary."""
        return attr.asdict(self)


class PodDriverBase(PowerDriverBase):
    """Base driver for a pod driver."""

    @abstractmethod
    def discover(self, context, system_id=None):
        """Discover the pod resources.

        :param context: Pod settings.
        :param system_id: Pod system_id.
        :returns: `Deferred` returning `DiscoveredPod`.
        :rtype: `twisted.internet.defer.Deferred`
        """

    @abstractmethod
    def compose(self, system_id, context, request):
        """Compose a node from parameters in context.

        :param system_id: Pod system_id.
        :param context: Pod settings.
        :param request: Requested machine.
        :type request: `RequestedMachine`.
        :returns: Tuple with (`DiscoveredMachine`, `DiscoveredPodHints`).
        """

    @abstractmethod
    def decompose(self, system_id, context):
        """Decompose a node.

        :param system_id: Pod system_id.
        :param context:  Pod settings.
        """

    def get_schema(self, detect_missing_packages=True):
        """Returns the JSON schema for the driver.

        Calculates the missing packages on each invoke.
        """
        schema = super(PodDriverBase, self).get_schema(
            detect_missing_packages=detect_missing_packages)
        schema['driver_type'] = 'pod'
        return schema


def get_error_message(err):
    """Returns the proper error message based on error."""
    if isinstance(err, PodAuthError):
        return "Could not authenticate to pod: %s" % err
    elif isinstance(err, PodConnError):
        return "Could not contact pod: %s" % err
    elif isinstance(err, PodActionError):
        return "Failed to complete pod action: %s" % err
    else:
        return "Failed talking to pod: %s" % err


class PodDriver(PowerDriver, PodDriverBase):
    """Default pod driver."""
