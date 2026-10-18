"""Class to hold all thermostat accessories."""

import logging
from typing import Any, override

from pyhap.const import CATEGORY_HUMIDIFIER
from pyhap.util import callback as pyhap_callback

from homeassistant.components.humidifier import (
    ATTR_HUMIDITY,
    ATTR_MODE,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_MIN_HUMIDITY,
    DOMAIN as HUMIDIFIER_DOMAIN,
    SERVICE_SET_HUMIDITY,
    SERVICE_SET_MODE,
    HumidifierDeviceClass,
    HumidifierEntityCapabilityAttribute,
    HumidifierEntityStateAttribute,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    PERCENTAGE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    EntityStateAttribute,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HassJobType,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .accessories import TYPES, HomeAccessory
from .const import (
    CHAR_ACTIVE,
    CHAR_CURRENT_HUMIDIFIER_DEHUMIDIFIER,
    CHAR_CURRENT_HUMIDITY,
    CHAR_DEHUMIDIFIER_THRESHOLD_HUMIDITY,
    CHAR_HUMIDIFIER_THRESHOLD_HUMIDITY,
    CHAR_ROTATION_SPEED,
    CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER,
    CONF_LINKED_HUMIDITY_SENSOR,
    PROP_MAX_VALUE,
    PROP_MIN_STEP,
    PROP_MIN_VALUE,
    SERV_HUMIDIFIER_DEHUMIDIFIER,
)

_LOGGER = logging.getLogger(__name__)

HC_AUTO = 0
HC_HUMIDIFIER = 1
HC_DEHUMIDIFIER = 2

CONF_LINKED_FAN_ENTITY = "linked_fan_entity"

HC_HASS_TO_HOMEKIT_DEVICE_CLASS = {
    HumidifierDeviceClass.HUMIDIFIER: HC_HUMIDIFIER,
    HumidifierDeviceClass.DEHUMIDIFIER: HC_DEHUMIDIFIER,
}

HC_HASS_TO_HOMEKIT_DEVICE_CLASS_NAME = {
    HumidifierDeviceClass.HUMIDIFIER: "Humidifier",
    HumidifierDeviceClass.DEHUMIDIFIER: "Dehumidifier",
}

HC_DEVICE_CLASS_TO_TARGET_CHAR = {
    HC_HUMIDIFIER: CHAR_HUMIDIFIER_THRESHOLD_HUMIDITY,
    HC_DEHUMIDIFIER: CHAR_DEHUMIDIFIER_THRESHOLD_HUMIDITY,
}


HC_STATE_INACTIVE = 0
HC_STATE_IDLE = 1
HC_STATE_HUMIDIFYING = 2
HC_STATE_DEHUMIDIFYING = 3

BASE_VALID_VALUES = {
    "Inactive": HC_STATE_INACTIVE,
    "Idle": HC_STATE_IDLE,
}

VALID_VALUES_BY_DEVICE_CLASS = {
    HumidifierDeviceClass.HUMIDIFIER: {
        **BASE_VALID_VALUES,
        "Humidifying": HC_STATE_HUMIDIFYING,
    },
    HumidifierDeviceClass.DEHUMIDIFIER: {
        **BASE_VALID_VALUES,
        "Dehumidifying": HC_STATE_DEHUMIDIFYING,
    },
}


@TYPES.register("HumidifierDehumidifier")
class HumidifierDehumidifier(HomeAccessory):
    """Generate a HumidifierDehumidifier accessory for a humidifier."""

    def __init__(self, *args: Any) -> None:
        """Initialize a HumidifierDehumidifier accessory object."""
        super().__init__(*args, category=CATEGORY_HUMIDIFIER)
        self._reload_on_change_attrs.extend(
            (
                HumidifierEntityCapabilityAttribute.MAX_HUMIDITY,
                HumidifierEntityCapabilityAttribute.MIN_HUMIDITY,
            )
        )

        self.chars: list[str] = []
        states = self.hass.states
        state = states.get(self.entity_id)
        assert state
        device_class = state.attributes.get(
            EntityStateAttribute.DEVICE_CLASS, HumidifierDeviceClass.HUMIDIFIER
        )
        self._hk_device_class = HC_HASS_TO_HOMEKIT_DEVICE_CLASS[device_class]

        self._target_humidity_char_name = HC_DEVICE_CLASS_TO_TARGET_CHAR[
            self._hk_device_class
        ]
        self.chars.append(self._target_humidity_char_name)

        # Look for linked fan entity (for fan speed control)
        self.linked_fan = self._find_linked_fan(state)
        self.char_rotation_speed = None
        if self.linked_fan:
            self.chars.append(CHAR_ROTATION_SPEED)

        serv_humidifier_dehumidifier = self.add_preload_service(
            SERV_HUMIDIFIER_DEHUMIDIFIER, self.chars
        )

        # Current and target mode characteristics
        self.char_current_humidifier_dehumidifier = (
            serv_humidifier_dehumidifier.configure_char(
                CHAR_CURRENT_HUMIDIFIER_DEHUMIDIFIER,
                value=0,
                valid_values=VALID_VALUES_BY_DEVICE_CLASS[device_class],
            )
        )

        # Check if entity supports auto mode
        available_modes = state.attributes.get(
            HumidifierEntityCapabilityAttribute.AVAILABLE_MODES
        ) or []
        self._supports_auto = "auto" in available_modes or (
            self.linked_fan
            and states.get(self.linked_fan) is not None
            and "auto" in (states.get(self.linked_fan).attributes.get("preset_modes") or [])
        )

        if self._supports_auto:
            if device_class == HumidifierDeviceClass.DEHUMIDIFIER:
                valid_target_values = {
                    "Auto": HC_AUTO,
                    "Dehumidifier": HC_DEHUMIDIFIER,
                }
                min_target = HC_AUTO
                max_target = HC_DEHUMIDIFIER
            else:
                valid_target_values = {
                    "Auto": HC_AUTO,
                    "Humidifier": HC_HUMIDIFIER,
                }
                min_target = HC_AUTO
                max_target = HC_HUMIDIFIER
        else:
            valid_target_values = {
                HC_HASS_TO_HOMEKIT_DEVICE_CLASS_NAME[
                    device_class
                ]: self._hk_device_class
            }
            min_target = self._hk_device_class
            max_target = self._hk_device_class

        self.char_target_humidifier_dehumidifier = (
            serv_humidifier_dehumidifier.configure_char(
                CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER,
                value=self._hk_device_class,
                properties={
                    PROP_MIN_VALUE: min_target,
                    PROP_MAX_VALUE: max_target,
                },
                valid_values=valid_target_values,
            )
        )

        # Current and target humidity characteristics
        self.char_current_humidity = serv_humidifier_dehumidifier.configure_char(
            CHAR_CURRENT_HUMIDITY, value=0
        )

        self.char_target_humidity = serv_humidifier_dehumidifier.configure_char(
            self._target_humidity_char_name,
            value=45,
            properties={
                PROP_MIN_VALUE: DEFAULT_MIN_HUMIDITY,
                PROP_MAX_VALUE: DEFAULT_MAX_HUMIDITY,
                PROP_MIN_STEP: 1,
            },
        )

        # Optional RotationSpeed characteristic (linked fan)
        if self.linked_fan:
            fan_state = states.get(self.linked_fan)
            step = 1
            init_speed = 0
            if fan_state:
                step = round(fan_state.attributes.get("percentage_step", 1))
                if step < 1:
                    step = 1
                init_speed = fan_state.attributes.get("percentage") or 0
            self.char_rotation_speed = serv_humidifier_dehumidifier.configure_char(
                CHAR_ROTATION_SPEED,
                value=init_speed,
                properties={
                    PROP_MIN_VALUE: 0,
                    PROP_MAX_VALUE: 100,
                    PROP_MIN_STEP: step,
                },
            )

        # Active/inactive characteristics
        self.char_active = serv_humidifier_dehumidifier.configure_char(
            CHAR_ACTIVE, value=False
        )

        self.async_update_state(state)

        serv_humidifier_dehumidifier.setter_callback = self._set_chars

        self.linked_humidity_sensor = self.config.get(CONF_LINKED_HUMIDITY_SENSOR)
        if self.linked_humidity_sensor:
            if humidity_state := states.get(self.linked_humidity_sensor):
                self._async_update_current_humidity(humidity_state)

    def _find_linked_fan(self, state: State) -> str | None:
        """Find a matching fan entity for this humidifier."""
        # 1. Config option: linked_fan_entity or linked_fan
        if linked_fan := self.config.get(CONF_LINKED_FAN_ENTITY) or self.config.get("linked_fan"):
            if self.hass.states.get(linked_fan):
                return linked_fan

        # 2. Match by entity_id: e.g. humidifier.xxx -> fan.xxx
        fan_entity_id = self.entity_id.replace("humidifier.", "fan.")
        if self.hass.states.get(fan_entity_id):
            return fan_entity_id

        # 3. Match by device_id in entity_registry
        try:
            ent_reg = er.async_get(self.hass)
            if entity_entry := ent_reg.async_get(self.entity_id):
                if entity_entry.device_id:
                    for entry in er.async_entries_for_device(ent_reg, entity_entry.device_id):
                        if entry.domain == "fan" and not entry.disabled_by:
                            if self.hass.states.get(entry.entity_id):
                                return entry.entity_id
        except Exception as ex:
            _LOGGER.debug("%s: Error finding linked fan via registry: %s", self.entity_id, ex)

        return None

    @callback
    @pyhap_callback  # type: ignore[untyped-decorator]
    @override
    def run(self) -> None:
        """Handle accessory driver started event.

        Run inside the Home Assistant event loop.
        """
        if self.linked_humidity_sensor:
            self._subscriptions.append(
                async_track_state_change_event(
                    self.hass,
                    [self.linked_humidity_sensor],
                    self.async_update_current_humidity_event,
                    job_type=HassJobType.Callback,
                )
            )

        if self.linked_fan:
            self._subscriptions.append(
                async_track_state_change_event(
                    self.hass,
                    [self.linked_fan],
                    self.async_update_fan_event,
                    job_type=HassJobType.Callback,
                )
            )

        super().run()

    @callback
    def async_update_fan_event(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle fan state change event listener callback."""
        self._async_update_fan(event.data["new_state"])

    @callback
    def _async_update_fan(self, new_state: State | None) -> None:
        """Handle linked fan state change to update HomeKit value."""
        if new_state is None or self.char_rotation_speed is None:
            return
        percentage = new_state.attributes.get("percentage")
        if isinstance(percentage, (int, float)):
            if self.char_rotation_speed.value != percentage:
                self.char_rotation_speed.set_value(percentage)

    @callback
    def async_update_current_humidity_event(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Handle state change event listener callback."""
        self._async_update_current_humidity(event.data["new_state"])

    @callback
    def _async_update_current_humidity(self, new_state: State | None) -> None:
        """Handle linked humidity sensor state change to update HomeKit value."""
        if new_state is None:
            _LOGGER.error(
                (
                    "%s: Unable to update from linked humidity sensor %s: the entity"
                    " state is None"
                ),
                self.entity_id,
                self.linked_humidity_sensor,
            )
            return
        try:
            current_humidity = float(new_state.state)
        except ValueError as ex:
            _LOGGER.debug(
                "%s: Unable to update from linked humidity sensor %s: %s",
                self.entity_id,
                self.linked_humidity_sensor,
                ex,
            )
            return
        self._async_update_current_humidity_value(current_humidity)

    @callback
    def _async_update_current_humidity_value(self, current_humidity: float) -> None:
        """Handle linked humidity or built-in humidity."""
        if self.char_current_humidity.value != current_humidity:
            _LOGGER.debug(
                "%s: Linked humidity sensor %s changed to %d",
                self.entity_id,
                self.linked_humidity_sensor,
                current_humidity,
            )
            self.char_current_humidity.set_value(current_humidity)

    def _set_chars(self, char_values: dict[str, Any]) -> None:
        """Set characteristics based on the data coming from HomeKit."""
        _LOGGER.debug("HumidifierDehumidifier _set_chars: %s", char_values)

        if CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER in char_values:
            hk_value = char_values[CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER]
            if self._supports_auto and hk_value == HC_AUTO:
                self.async_call_service(
                    HUMIDIFIER_DOMAIN,
                    SERVICE_SET_MODE,
                    {ATTR_ENTITY_ID: self.entity_id, ATTR_MODE: "auto"},
                    f"{CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER} to Auto",
                )
                if self.linked_fan:
                    fan_s = self.hass.states.get(self.linked_fan)
                    if fan_s and "auto" in (fan_s.attributes.get("preset_modes") or []):
                        self.async_call_service(
                            "fan",
                            "set_preset_mode",
                            {ATTR_ENTITY_ID: self.linked_fan, "preset_mode": "auto"},
                            "fan preset_mode to auto",
                        )
            elif hk_value == HC_DEHUMIDIFIER and self._hk_device_class == HC_DEHUMIDIFIER:
                self.async_call_service(
                    HUMIDIFIER_DOMAIN,
                    SERVICE_SET_MODE,
                    {ATTR_ENTITY_ID: self.entity_id, ATTR_MODE: "dehumidify"},
                    f"{CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER} to Dehumidify",
                )
                if self.linked_fan:
                    fan_s = self.hass.states.get(self.linked_fan)
                    if fan_s and "dehumidify" in (fan_s.attributes.get("preset_modes") or []):
                        self.async_call_service(
                            "fan",
                            "set_preset_mode",
                            {ATTR_ENTITY_ID: self.linked_fan, "preset_mode": "dehumidify"},
                            "fan preset_mode to dehumidify",
                        )
            elif hk_value == HC_HUMIDIFIER and self._hk_device_class == HC_HUMIDIFIER:
                self.async_call_service(
                    HUMIDIFIER_DOMAIN,
                    SERVICE_SET_MODE,
                    {ATTR_ENTITY_ID: self.entity_id, ATTR_MODE: "normal"},
                    f"{CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER} to Humidifier",
                )
            elif self._hk_device_class != hk_value:
                _LOGGER.error(
                    "%s is not supported: %s", CHAR_TARGET_HUMIDIFIER_DEHUMIDIFIER, hk_value
                )

        if CHAR_ACTIVE in char_values:
            is_active = bool(char_values[CHAR_ACTIVE])
            self.async_call_service(
                HUMIDIFIER_DOMAIN,
                SERVICE_TURN_ON if is_active else SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: self.entity_id},
                f"{CHAR_ACTIVE} to {is_active}",
            )
            if self.linked_fan:
                self.async_call_service(
                    "fan",
                    "turn_on" if is_active else "turn_off",
                    {ATTR_ENTITY_ID: self.linked_fan},
                    f"fan {CHAR_ACTIVE} to {is_active}",
                )

        if self.char_rotation_speed and CHAR_ROTATION_SPEED in char_values:
            speed = char_values[CHAR_ROTATION_SPEED]
            if self.linked_fan:
                self.async_call_service(
                    "fan",
                    "set_percentage",
                    {ATTR_ENTITY_ID: self.linked_fan, "percentage": speed},
                    f"{CHAR_ROTATION_SPEED} to {speed}%",
                )

        if self._target_humidity_char_name in char_values:
            state = self.hass.states.get(self.entity_id)
            assert state
            min_humidity, max_humidity = self.get_humidity_range(state)
            humidity = round(char_values[self._target_humidity_char_name])

            if (humidity < min_humidity) or (humidity > max_humidity):
                humidity = min(max_humidity, max(min_humidity, humidity))
                # Update the HomeKit value to the clamped humidity,
                # so the user will get visual feedback that they
                # cannot set to a value below/above the min/max.
                self.char_target_humidity.set_value(humidity)

            self.async_call_service(
                HUMIDIFIER_DOMAIN,
                SERVICE_SET_HUMIDITY,
                {ATTR_ENTITY_ID: self.entity_id, ATTR_HUMIDITY: humidity},
                (
                    f"{self._target_humidity_char_name} to "
                    f"{char_values[self._target_humidity_char_name]}{PERCENTAGE}"
                ),
            )

    def get_humidity_range(self, state: State) -> tuple[int, int]:
        """Return min and max humidity range."""
        attributes = state.attributes
        min_humidity = max(
            round(
                attributes.get(
                    HumidifierEntityCapabilityAttribute.MIN_HUMIDITY,
                    DEFAULT_MIN_HUMIDITY,
                )
            ),
            0,
        )
        max_humidity = min(
            round(
                attributes.get(
                    HumidifierEntityCapabilityAttribute.MAX_HUMIDITY,
                    DEFAULT_MAX_HUMIDITY,
                )
            ),
            100,
        )
        return min_humidity, max_humidity

    @callback
    @override
    def async_update_state(self, new_state: State) -> None:
        """Update state without rechecking the device features."""
        is_active = new_state.state == STATE_ON
        attributes = new_state.attributes

        # Update active state
        self.char_active.set_value(is_active)

        # Set current state
        if is_active:
            if self._hk_device_class == HC_HUMIDIFIER:
                current_state = HC_STATE_HUMIDIFYING
            else:
                current_state = HC_STATE_DEHUMIDIFYING
        else:
            current_state = HC_STATE_INACTIVE
        self.char_current_humidifier_dehumidifier.set_value(current_state)

        # Update target state
        if self._supports_auto:
            current_mode = attributes.get(HumidifierEntityStateAttribute.MODE)
            if current_mode == "auto":
                self.char_target_humidifier_dehumidifier.set_value(HC_AUTO)
            else:
                self.char_target_humidifier_dehumidifier.set_value(self._hk_device_class)

        # Update target humidity
        target_humidity = attributes.get(HumidifierEntityStateAttribute.HUMIDITY)
        if isinstance(target_humidity, (int, float)):
            self.char_target_humidity.set_value(target_humidity)
        current_humidity = attributes.get(
            HumidifierEntityStateAttribute.CURRENT_HUMIDITY
        )
        if isinstance(current_humidity, (int, float)):
            self.char_current_humidity.set_value(current_humidity)

        # Update rotation speed from linked fan
        if self.char_rotation_speed and self.linked_fan:
            if fan_state := self.hass.states.get(self.linked_fan):
                speed = fan_state.attributes.get("percentage")
                if isinstance(speed, (int, float)):
                    self.char_rotation_speed.set_value(speed)
