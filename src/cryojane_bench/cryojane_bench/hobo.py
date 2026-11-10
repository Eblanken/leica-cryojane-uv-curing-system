"""Decode Onset HOBOware .hobo data files.

The container (magic, header tags, start of data) is described by hobo.ksy.
This module re-implements that part in plain Python so it has no
dependencies, and adds what Kaitai Struct cannot express: the data region is
a big-endian bit stream whose field widths depend on the logger model and on
the channels enabled at launch.

Ported from HOBOware 3.7 (OLServices.logger.usb.wrap.phoenix.OLoggerUSBPhoenix
.parseSensorData, OLServices.OEvent.parseEventIntoObject,
OLServices.logger.usb.wrap.OLoggerUFWrap). Supported models:

    PID 9218  MX1101  temperature / RH (Bluetooth)

Usage:
    python hobo.py FILE.hobo                 # summary
    python hobo.py FILE.hobo --csv out.csv   # samples as CSV
    python hobo.py FILE.hobo --header        # dump header tags
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


# --------------------------------------------------------------------------
# Header tags
# --------------------------------------------------------------------------

TAG_NAMES = {
    0x8801: 'usb_vid',
    0x8802: 'usb_pid',
    0x8803: 'usb_version',
    0x8804: 'usb_mfg',
    0x8805: 'usb_product',
    0x8806: 'usb_serial',
    0x8807: 'start_time',
    0x8808: 'interval',
    0x8809: 'delay',
    0x880A: 'description',
    0x880B: 'enabled',
    0x880C: 'selected',
    0x880D: 'start_of_data',
    0x880E: 'launch_count',
    0x880F: 'creation_date',
    0x8811: 'group_end',
    0x8812: 'start_tz_offset',
    0x8813: 'start_tz_is_dst',
    0x8814: 'start_tz_string',
    0x8815: 'event_channel',
    0x8816: 'channel',
    0x8817: 'group_index',
    0x8818: 'property',
    0x8819: 'event_value',
    0x881A: 'attachment',
    0x881B: 'dtf_behavior',
    0x881C: 'dtf_version',
    0x881D: 'end_of_header',
    0x881E: 'start_battery',
    0x881F: 'device_size',
    0x8820: 'attachment_name',
    0x8821: 'unit',
    0x8822: 'shuttle_info',
    0x8823: 'lockout_low',
    0x8824: 'lockout_high',
    0x8825: 'alarm_high',
    0x8826: 'alarm_low',
    0x8827: 'alarm_style',
    0x8828: 'interval_group',
    0x8829: 'points',
    0x882A: 'object_id',
    0x882B: 'alarm_low_real',
    0x882C: 'alarm_high_real',
    0x882D: 'alarm_low_display',
    0x882E: 'alarm_high_display',
    0x8830: 'eeprom',
    0x8831: 'channel_calib',
    0x8832: 'crystal_calib',
    0x8833: 'calibration_date',
    0x8834: 'launcher',
    0x8835: 'relaunch_commands',
    0x8836: 'linear_scale_x0',
    0x8837: 'linear_scale_x1',
    0x8838: 'linear_scale_y0',
    0x8839: 'linear_scale_y1',
    0x883B: 'username',
    0x8840: 'oser_readout_event',
    0x8841: 'datafile_pointer',
    0x8842: 'datafile_used',
    0x8843: 'logger_serial',
    0x8844: 'shuttle_clock',
    0x8845: 'logger_clock',
    0x8848: 'logger_battery',
    0x8849: 'shuttle_main_batt',
    0x884E: 'usb_readout_event',
    0x884F: 'start_of_index',
    0x8850: 'shuttle_backup_batt',
    0x8851: 'shuttle_temp',
    0x8853: 'prev_readout_total',
    0x8854: 'start_backup_battery',
    0x8858: 'accel_calib',
    0x8859: 'gain',
    0x885A: 'interval_divisor',
    0x885B: 'phase_calib',
    0x885C: 'relay_normally_closed',
    0x885D: 'keep_relay_tripped',
    0x885E: 'relay_on_low_batt',
    0x885F: 'display_metric',
    0x8860: 'eeprom_serial_string',
    0x8861: 'sample_interval',
    0x8862: 'trigger_threshold',
    0x8863: 'wrap',
    0x8864: 'da_data',
    0x8865: 'da_id',
    0x8866: 'da_raw_series',
    0x8867: 'da_serial',
    0x8868: 'da_sub_serial',
    0x8869: 'da_version',
    0x886A: 'da_series_name',
    0x886B: 'da_params',
    0x886C: 'da_series_notes',
    0x886D: 'water_density',
    0x886E: 'water_temp',
    0x886F: 'temperature_trim',
    0x8870: 'filter_data',
    0x8871: 'filter_raw_series',
    0x8872: 'filter_serial',
    0x8873: 'filter_sub_serial',
    0x8874: 'filter_source',
    0x8875: 'filter_property',
    0x8876: 'filter_series_name',
    0x8877: 'filter_type',
    0x8878: 'filter_multiplier',
    0x8879: 'filter_base_interval',
    0x887A: 'eeprom_id',
    0x887B: 'eeprom_rev',
    0x887C: 'device_fw_id',
    0x887D: 'device_fw_minor',
    0x887E: 'reserved',
    0x887F: 'reserved',
    0x8888: 'group_start',
    0x8890: 'channel_bitsize',
    0x8891: 'stop_time',
    0x8892: 'stop_btn',
    0x8893: 'stealth_mode',
    0x8894: 'pulse_lockout',
    0x8895: 'channel_event_name',
    0x8896: 'channel_open_name',
    0x8897: 'channel_closed_name',
    0x8898: 'channel_runtime_name',
    0x8899: 'channel_runtime_units',
    0x889A: 'channel_units_description',
    0x889B: 'channel_device_type',
    0x889C: 'channel_scaling_selector',
    0x889D: 'lockout_time',
    0x889E: 'lockout_map',
    0x889F: 'max_pulse_frequency',
    0x88A0: 'bhw_version',
    0x88A1: 'unit_scale_text',
    0x88A2: 'lcd_tou_mode',
    0x88A3: 'state_power_save_mode',
    0x88A4: 'state_calibration_option',
    0x88A5: 'lockout_time32',
    0x88A6: 'lcd_scale',
    0x88A7: 'steinhart_hart_a',
    0x88A8: 'steinhart_hart_b',
    0x88A9: 'steinhart_hart_c',
    0x88AA: 'steinhart_hart_d',
    0x88AB: 'steinhart_hart_ohms',
    0x88B0: 'min_max_avg_stddev',
    0x88B1: 'btn_restart',
    0x88B2: 'alarm_hysteresis_mode',
    0x88B3: 'maintain_alarm_mode',
    0x88B4: 'alarm_burst_mode',
    0x88B5: 'channel_name',
    0x88B6: 'group_name',
    0x88B7: 'start_type',
    0x88B8: 'stop_type',
    0x88B9: 'logger_location_lat',
    0x88BA: 'logger_location_long',
    0x88BB: 'advertisement_interval',
    0x88BC: 'log_stats_only',
    0x88BD: 'interface_fw_version',
    0x88BE: 'channel_statistics',
    0x88BF: 'channel_cal_multiplier',
    0x88C0: 'channel_cal_offset',
    0x88C1: 'alarm_chirp_properties',
    0x88C2: 'use_co2_auto_cal',
    0x88C3: 'use_co2_alt_compensation',
    0x88C4: 'altitude_value',
    0x88C5: 'altitude_units_english',
    0x88C6: 'use_co2_manual_cal',
    0x88C7: 'ref_water_level',
    0x88C8: 'ref_water_level_time',
    0x88C9: 'rem_sensor_sn',
    0x88CA: 'rem_sensor_fw_version',
    0x88CB: 'ref_water_level_units',
    0x88CC: 'water_density_units',
    0x88CD: 'calc_water_level',
    0x88CE: 'rem_sensor_pid',
    0x88CF: 'rem_sensor_calib_date',
    0x88D0: 'alarm_hysteresis_low',
    0x88D1: 'alarm_hysteresis_high',
    0x88D2: 'user_id',
    0x88D3: 'account_id',
    0x88D4: 'filter_start_day',
    0x88D5: 'powersave_ble_mode',
    0x88D6: 'configuring_webservice_url',
    0x88D7: 'configuration_id',
    0x88D8: 'water_detect_ble_mode',
    0x88D9: 'ph_cal_slope',
    0x88DA: 'ph_cal_offset',
    0x88DB: 'ph_cal_date',
    0x88DC: 'ph_cal_buffers',
    0x88DD: 'include_location_in_events',
    0x88DE: 'gateway_logger',
    0x88DF: 'cloud_config_info',
    0x88E0: 'conductivity_calibration',
    0x88E1: 'calibration_points',
    0xFFFF: 'end',
}

TAG_GROUP_START = 0x8888
TAG_GROUP_END = 0x8811
TAG_END = 0xFFFF
# OLoggerUSB.m_groupHeadingTags: tags whose children follow in a
# group_start ... group_end block.
GROUP_HEADINGS = {0x8815, 0x8816, 0x8828, 0x8830, 0x8840, 0x884E, 0x8857,
                  0x8864, 0x8870, 0x88E0}


class HoboFormatError(Exception):
    pass


@dataclass
class Tag:
    id: int
    data: bytes
    children: list = field(default_factory=list)

    @property
    def name(self):
        return TAG_NAMES.get(self.id, f'unknown_{self.id:04x}')

    def as_int(self):
        """OLoggerUtilities.convertToInteger: big-endian, wraps to int32."""
        v = int.from_bytes(self.data, 'big') if self.data else 0
        if len(self.data) == 4 and v & 0x80000000:
            v -= 1 << 32
        return v

    def find(self, tag_id, index=0):
        """OTagDataGroup.getTagInGroup: search this group's direct children."""
        hits = [t for t in self.children if t.id == tag_id]
        return hits[index] if index < len(hits) else None


def _read_tag(buf, pos):
    """OTagDataList.extractNextTag. Returns (tag, next_pos)."""
    if pos + 1 >= len(buf):
        raise HoboFormatError(f'header runs past end of file at {pos}')
    id_len = 2 if buf[pos] & 0x80 else 1
    tag_id = int.from_bytes(buf[pos:pos + id_len], 'big')
    if tag_id == TAG_END:
        return Tag(TAG_END, b''), pos + 2
    pos += 2  # always two bytes, whatever id_len was
    len_len = 2 if buf[pos] & 0x80 else 1
    n = int.from_bytes(buf[pos:pos + len_len], 'big')
    if len_len == 2:
        n &= 0x7FFF
    pos += len_len
    return Tag(tag_id, bytes(buf[pos:pos + n])), pos + n


def parse_header(buf):
    """Parse the tag list after the "HOBO" magic.

    Returns (top_level_tags, end_pos). A group-heading tag gets the tags of
    its group_start ... group_end block as children, as OTagDataList does.
    """
    if bytes(buf[:4]) != b'HOBO':
        raise HoboFormatError('missing "HOBO" magic')
    pos = 4

    def read_group(heading, pos):
        depth = 0
        tag, pos = _read_tag(buf, pos)
        while True:
            if tag.id == TAG_GROUP_START:
                depth += 1
            elif tag.id == TAG_GROUP_END:
                depth -= 1
            if tag.id in GROUP_HEADINGS:
                tag, pos = read_group(tag, pos)
            heading.children.append(tag)
            if tag.id == TAG_END:
                return heading, pos
            nxt, nxt_pos = _read_tag(buf, pos)
            if depth == 0 and nxt.id != TAG_GROUP_START:
                return heading, pos  # leave nxt for the caller
            tag, pos = nxt, nxt_pos

    tags = []
    while True:
        tag, pos = _read_tag(buf, pos)
        if tag.id == TAG_END:
            return tags, pos
        if tag.id in GROUP_HEADINGS:
            tag, pos = read_group(tag, pos)
        tags.append(tag)


class Header:
    """Top-level tag lookups with OTagDataList's defaults for missing tags."""

    def __init__(self, tags):
        self.tags = tags

    def get(self, tag_id, index=0):
        hits = [t for t in self.tags if t.id == tag_id]
        return hits[index] if index < len(hits) else None

    def all(self, tag_id):
        return [t for t in self.tags if t.id == tag_id]

    def int(self, tag_id, default=-1):
        t = self.get(tag_id)
        return t.as_int() if t is not None else default

    def byte(self, tag_id):
        """getTagByteValue: signed byte, -1 when missing."""
        t = self.get(tag_id)
        if t is None:
            return -1
        v = t.as_int() & 0xFF
        return v - 256 if v > 127 else v

    def bool(self, tag_id):
        return self.int(tag_id) > 0

    def str(self, tag_id, default=''):
        t = self.get(tag_id)
        return t.data.decode('latin-1') if t is not None else default

    def date(self, tag_id, tz):
        t = self.get(tag_id)
        if t is None:
            return None
        b = t.data
        return datetime(b[0] * 100 + b[1], b[2], b[3], b[4], b[5], b[6],
                        tzinfo=tz)


def dump_header(tags, indent=0, out=sys.stdout):
    for t in tags:
        if t.children:
            print(f"{'  ' * indent}{t.id:04X} {t.name}", file=out)
            dump_header(t.children, indent + 1, out)
            continue
        d = t.data
        if d and all(32 <= c < 127 for c in d):
            val = d.decode('ascii')
        elif len(d) in (1, 2, 4):
            val = f'{t.as_int()} (0x{d.hex()})'
        else:
            val = d.hex()
        print(f"{'  ' * indent}{t.id:04X} {t.name:<24} {val}", file=out)


# --------------------------------------------------------------------------
# Bit stream
# --------------------------------------------------------------------------

def get_bits(buf, bit_pos, n):
    """OSample.intGetXBits/longGetXBits: n bits, MSB first, from bit_pos."""
    if n == 0:
        return 0
    end = bit_pos + n
    if bit_pos < 0 or end > len(buf) * 8:
        raise IndexError(f'bit read {bit_pos}+{n} past end of buffer')
    first, last = bit_pos >> 3, (end - 1) >> 3
    v = int.from_bytes(buf[first:last + 1], 'big')
    return (v >> ((last + 1) * 8 - end)) & ((1 << n) - 1)


# --------------------------------------------------------------------------
# Events (Phoenix / UX / MX encoding)
# --------------------------------------------------------------------------

EVENT_TYPES = {
    6: 'Line 1 Open', 7: 'Line 1 Closed', 8: 'Line 2 Open',
    9: 'Line 2 Closed', 10: 'Line 3 Open', 11: 'Line 3 Closed',
    12: 'Line 4 Open', 13: 'Line 4 Closed', 14: 'Extended Event',
    15: 'End Of File',
}
# OLoggerUSBPhoenix.m_customEvents (subcode of extended event type 14)
EXTENDED_EVENTS = {
    0: 'Host Connected', 1: 'Started', 2: 'Stopped', 3: 'Button Up',
    4: 'Button Down', 5: 'Bad Battery', 6: 'Good Battery',
    7: 'Safe Shutdown - Low Battery', 8: 'Calibration Button Up',
    9: 'Calibration Button Down', 15: 'Custom Event', 16: 'New Interval',
    17: 'Block Count', 18: 'Alarm Count', 19: 'BEH End',
    24: 'Chan 1 Alarm Tripped', 25: 'Chan 1 Alarm Cleared',
    26: 'Chan 2 Alarm Tripped', 27: 'Chan 2 Alarm Cleared',
    28: 'Chan 3 Alarm Tripped', 29: 'Chan 3 Alarm Cleared',
    30: 'Chan 4 Alarm Tripped', 31: 'Chan 4 Alarm Cleared',
    32: 'Internal Calibration', 33: 'Host Calibration',
    34: 'Calibration Failure', 35: 'Data Event', 36: 'Started Offset',
    37: 'Next Sample', 38: 'Line Loss', 39: 'Line Resume',
    40: 'Reference Level', 41: 'Water Density', 42: 'Reference Time',
    43: 'Probe Removed', 44: 'Probe Attached', 45: 'Check Missed',
    46: 'Water Detect', 47: 'Low Power Warning', 48: 'Start pH Calibration',
    49: 'End pH Calibration', 50: 'Cancel pH Calibration',
    51: 'pH Calibration Values', 52: 'Battery Data',
    53: 'Chan 1 Burst Data', 54: 'Chan 2 Burst Data',
    55: 'Chan 3 Burst Data', 56: 'Chan 4 Burst Data', 57: 'End Burst Data',
    74: 'pH Calibration Time', 75: 'pH Calibration Buffers',
    76: 'Sensor Plugged', 77: 'Sensor Unplugged',
}
ALARM_CLEAR_SUBCODES = {25, 27, 29, 31}
SUB_DATA_EVENT = 35


@dataclass
class RawEvent:
    type: int
    subcode: int = 0
    temporal_offset: int = 0     # seconds since the previous item
    data: int = 0                # extended event payload
    data_len: int = 0            # payload length in nibbles
    end_bit: int = 0
    valid: bool = True
    complete: bool = True

    @property
    def name(self):
        if self.type == 14:
            return EXTENDED_EVENTS.get(self.subcode, f'Extended {self.subcode}')
        return EVENT_TYPES.get(self.type, f'Event {self.type}')


def parse_event(buf, pos, flag_bits, flag):
    """OEvent.parseEventIntoObject for Phoenix devices.

    `pos` is the bit just after the all-ones flag field. Layout:
        type:4
        type 15            -> nothing more
        type 0..13         -> len:4, offset:len*4
        type 14 (extended) -> subcode:8, tlen:4, offset:tlen*4,
                              dlen:4, data:dlen*4
    An alarm-cleared event swallows an immediately following data event.
    """
    ev = RawEvent(get_bits(buf, pos, 4))
    pos += 4
    if ev.type != 14:
        if ev.type != 15:
            n = get_bits(buf, pos, 4)
            pos += 4
            ev.temporal_offset = get_bits(buf, pos, n * 4)
            pos += n * 4
        ev.end_bit = pos
        return ev
    ev.subcode = get_bits(buf, pos, 8)
    pos += 8
    n = get_bits(buf, pos, 4)
    pos += 4
    ev.temporal_offset = get_bits(buf, pos, n * 4)
    pos += n * 4
    ev.data_len = get_bits(buf, pos, 4)
    pos += 4
    ev.data = get_bits(buf, pos, ev.data_len * 4)
    pos += ev.data_len * 4
    if ev.subcode in (40, 41):
        # reference level / water density: followed by a data event
        if get_bits(buf, pos, flag_bits) == flag:
            nxt = parse_event(buf, pos + flag_bits, flag_bits, flag)
            if nxt.type == 14 and nxt.subcode == SUB_DATA_EVENT:
                pos = nxt.end_bit
    if ev.subcode in ALARM_CLEAR_SUBCODES:
        if get_bits(buf, pos, flag_bits) == flag:
            nxt = parse_event(buf, pos + flag_bits, flag_bits, flag)
            if nxt.type == 14 and nxt.subcode == SUB_DATA_EVENT:
                pos = nxt.end_bit
                ev.data, ev.data_len = nxt.data, nxt.data_len
            else:
                ev.data, ev.data_len, ev.complete = 0, 8, False
        else:
            ev.complete = False
    ev.end_bit = pos
    return ev


# --------------------------------------------------------------------------
# Wrapped memory: block event headers (OLoggerUFWrap)
# --------------------------------------------------------------------------

@dataclass
class BlockHeader:
    page: int
    data: bytes
    seconds_since_launch: int = 0
    seconds_since_launch_button_start: int = 0
    seconds_to_next_sample_burst: int = 0
    interval: int = 0
    block_count: int = 0
    sample_bit_address: int = 0  # relative to the start of `data`


def read_block_header(buf, page, page_size, flag_bits):
    """OLoggerUFWrap.getBlockEventHeader for Phoenix devices."""
    base = page * page_size
    if all(b == 0xFF for b in buf[base:base + 8]):
        return None  # unused page
    flag = (1 << flag_bits) - 1
    beh = BlockHeader(page, bytes(buf[base:base + page_size]))
    start = base * 8
    pos = start
    end = start + page_size * 8
    while pos + flag_bits < end:
        if get_bits(buf, pos, flag_bits) != flag:
            raise HoboFormatError(f'corrupted data block {page}')
        ev = parse_event(buf, pos + flag_bits, flag_bits, flag)
        pos = ev.end_bit
        if ev.type == 14:
            if ev.subcode == 1:
                beh.seconds_since_launch = ev.temporal_offset
            elif ev.subcode == 16:
                beh.interval = ev.data & 0xFFFF
            elif ev.subcode == 17:
                beh.block_count = ev.data
            elif ev.subcode == 19:
                beh.sample_bit_address = ev.end_bit - start
                return beh
            elif ev.subcode == 36:
                beh.seconds_since_launch_button_start = ev.data
            elif ev.subcode == 37:
                beh.seconds_to_next_sample_burst = ev.data
    raise HoboFormatError(f'no BEH end in data block {page}')


def reassemble_wrapped(buf, page_size, flag_bits):
    """OLoggerUFWrap.reassembleWrappedData: BEHs ordered oldest first."""
    pages = -(-len(buf) // page_size)
    behs = []
    for page in range(1, pages):  # page 0 holds the launch header
        try:
            beh = read_block_header(buf, page, page_size, flag_bits)
        except (HoboFormatError, IndexError):
            beh = None
        if beh is None:
            break
        behs.append(beh)
    while behs and behs[0].block_count > behs[-1].block_count:
        behs.append(behs.pop(0))
    return behs


# --------------------------------------------------------------------------
# Measurement conversions (OLServices.measurement.*)
# --------------------------------------------------------------------------

def round_to(x, step):
    """ULGHelp.roundTo."""
    r = abs(x) % step
    if abs(x) < step:
        return x
    if r < step / 2:
        return x + r if x < 0 else x - r
    return x - (step - r) if x < 0 else x + (step - r)


def thermistor_c(raw, full_raw=4096.0, r_ref=10000.0, r_par=math.inf):
    """OMTemp_Thermistor.TempC: 10k thermistor divider -> deg C."""
    x = raw + 0.5
    if r_par == math.inf:
        r = r_ref * x / (full_raw - x)
    else:
        hi = (r_par + 10000.0) / (r_par + 20000.0)
        lo = 10000.0 / (r_par + 20000.0)
        v = lo + (hi - lo) * x / full_raw
        r = r_ref * v / (1.0 - v)
    lr = math.log10(r)
    return (560.112 - lr * 267.344 + lr * 51.576 * lr
            - lr * 5.539 * lr * lr + lr * 0.24814 * lr * lr * lr)


def temp_thermistor(raw):
    """OMTemp_Thermistor() as used by the MX1101 (12-bit)."""
    return round_to(thermistor_c(raw[0]), 0.001)


def temp_thermistor_sd(raw):
    """OMTemp_ThermistorStdDev: raw = [average raw, std-dev raw]."""
    avg, sd = raw[0], raw[1] if len(raw) > 1 else 0
    return round_to(abs(thermistor_c(avg - sd) - thermistor_c(avg)), 0.001)


def rh_ge_chipcap2(raw):
    """OMRH_GEChipCap2: 14-bit linear %RH."""
    if raw[0] in (16382, 16383):
        return round_to(100.0, 0.001)
    return round_to(raw[0] / 16384.0 * 100.0, 0.001)


def battery_type11(raw):
    """OMBattery_Type11: 8-bit, 5 V / 256 per count."""
    return round_to(raw[0] * 0.01953125, 0.01)


def dewpoint_c(temp_c, rh):
    """OMDewPoint_Sensirion.StdDewPointC (Magnus formula)."""
    if rh < 1e-6:
        return -273.0
    if rh > 100.0:
        return temp_c
    es = 6.1078 * 10 ** (temp_c * 7.5 / (temp_c + 237.3))
    e = rh * es / 100.0
    return (-430.22 + 237.7 * math.log(e)) / (-math.log(e) + 19.08)


def dewpoint_ge_chipcap2(raw):
    """OMDewPoint_GEChipCap2: raw = [temperature raw, RH raw]."""
    t = thermistor_c(raw[0])
    if raw[1] in (16382, 16383):
        rh = 100.0
    else:
        rh = raw[1] / 16384.0 * 100.0
    return round_to(dewpoint_c(t, rh), 0.001)


# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------

@dataclass
class Channel:
    key: str                  # stable id, e.g. 'temp', 'temp_max'
    name: str                 # HOBOware description
    unit: str
    bits: int                 # 0 for derived channels
    xfer: object              # conversion function taking a list of raws
    enabled: bool = True
    physical: int = -1        # physical channel index (burst data events)
    secondaries: list = field(default_factory=list)  # keys, in xfer order

    @property
    def real(self):
        return self.bits > 0


def _channel_enabled(header, index):
    """OChannelTag.getGroupData: ENABLED==1 in the index-th CHANNEL group."""
    groups = header.all(0x8816)
    if index >= len(groups):
        return False
    t = groups[index].find(0x880B)
    return t is not None and t.as_int() == 1


@dataclass
class LaunchOptions:
    stats_max: bool = False
    stats_min: bool = False
    stats_avg: bool = False
    stats_sd: bool = False
    stats_only: bool = False
    burst: bool = False


def _stats_channels(base, opts, sd_xfer, sd_uses_avg):
    """OLoggerUSBMX.setUpLoggerFilteredChannel for one base channel."""
    out = []
    if not base.enabled:
        return out
    for flag, suffix, label in ((opts.stats_max, 'max', 'Maximum'),
                                (opts.stats_min, 'min', 'Minimum'),
                                (opts.stats_avg, 'avg', 'Average')):
        if flag:
            out.append(Channel(f'{base.key}_{suffix}',
                               f'{base.name} - {label}', base.unit,
                               base.bits, base.xfer))
    if opts.stats_sd:
        sd = Channel(f'{base.key}_sd', f'{base.name} - Standard Deviation',
                     base.unit, base.bits, sd_xfer)
        if sd_uses_avg and opts.stats_avg:
            sd.secondaries = [f'{base.key}_avg', sd.key]
        out.append(sd)
    return out


def channels_mx1101(header, opts):
    """OLoggerUF9218.setUpChannels: temperature, RH, battery, dew point."""
    temp = Channel('temp', 'Temperature', '°C', 12, temp_thermistor,
                   _channel_enabled(header, 0), physical=0)
    rh = Channel('rh', 'RH', '%', 14, rh_ge_chipcap2,
                 _channel_enabled(header, 1), physical=1)
    batt = Channel('battery', 'Battery', 'V', 8, battery_type11,
                   _channel_enabled(header, 2), physical=2)
    dew = Channel('dewpoint', 'Dew Point', '°C', 0, dewpoint_ge_chipcap2,
                  temp.enabled and rh.enabled, secondaries=['temp', 'rh'])
    chans = [temp]
    chans += _stats_channels(temp, opts, temp_thermistor_sd, sd_uses_avg=True)
    chans.append(rh)
    # RH has no std-dev class of its own; HOBOware clones the RH measurement
    chans += _stats_channels(rh, opts, rh_ge_chipcap2, sd_uses_avg=False)
    chans += [batt, dew]
    if opts.stats_only:
        for c in (temp, rh, dew):
            c.enabled = False
    return chans


# PID -> (model, channel setup, wrap page size from setMemoryParams)
MODELS = {
    9218: ('MX1101', channels_mx1101, 4096),
}


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------

@dataclass
class Sample:
    time: datetime
    values: dict              # channel key -> value (missing if not logged)
    burst: bool = False


@dataclass
class Event:
    time: datetime
    name: str
    type: int
    subcode: int
    data: int


@dataclass
class HoboFile:
    header: Header
    model: str
    pid: int
    serial: str
    launch_time: datetime
    interval: float
    channels: list
    samples: list
    events: list
    warnings: list


def _launch_info(h):
    """The parts of OLoggerUSB.extractHeaderData that the parser needs."""
    tz = timezone(timedelta(seconds=h.int(0x8812, 0)))
    launch = h.date(0x8807, tz)
    if launch is None:
        raise HoboFormatError('no start_time tag')
    interval = h.int(0x8808)
    if interval == -1:
        grp = h.get(0x8828)
        t = grp.find(0x8808) if grp else None
        interval = t.as_int() if t else 0
    divisor = abs(h.int(0x885A))
    if divisor > 1:
        interval /= divisor
    opts = LaunchOptions()
    bits = h.byte(0x88B0)
    if bits != -1:
        opts.stats_sd = bool(bits & 1)
        opts.stats_avg = bool(bits & 2)
        opts.stats_max = bool(bits & 4)
        opts.stats_min = bool(bits & 8)
    opts.stats_only = h.bool(0x88BC)
    opts.burst = h.byte(0x88B4) == 2
    return launch, interval, h.int(0x8809), opts


def decode(buf):
    """Decode a .hobo file image (bytes) into a HoboFile."""
    tags, _ = parse_header(buf)
    h = Header(tags)
    pid = h.int(0x8802)
    if pid not in MODELS:
        known = ', '.join(f'{p} ({m[0]})' for p, m in MODELS.items())
        raise HoboFormatError(
            f'PID {pid} ({h.str(0x8805)}) is not supported; known: {known}')
    model, setup, page_size = MODELS[pid]
    launch, interval, delay, opts = _launch_info(h)
    chans = setup(h, opts)
    samples, events, warnings = _parse_phoenix(buf, h, chans, launch,
                                               int(interval), delay, opts,
                                               page_size)
    return HoboFile(h, model, pid, h.str(0x8806).strip(), launch, interval,
                    [c for c in chans if c.enabled], samples, events, warnings)


def _parse_phoenix(buf, h, chans, launch, interval, delay, opts, page_size):
    """OLoggerUSBPhoenix.parseSensorData, for loggers without event/state
    channels (types 9/10), which covers the MX1101."""
    warnings = []
    used = [c for c in chans if c.enabled]
    stream = [c for c in used if c.real]
    bits_per_sample = sum(c.bits for c in stream)
    by_key = {c.key: c for c in chans}
    first_bits = stream[0].bits if stream else 0
    flag = (1 << first_bits) - 1
    flag_width = first_bits + 4

    delay = max(delay, 0)
    first_sample = launch + timedelta(seconds=delay)
    sample_time = first_sample - timedelta(seconds=interval)
    event_time = first_sample

    wrap = h.bool(0x8863)
    behs = []
    block = 0
    first_block_after_wrap = False
    if wrap:
        behs = reassemble_wrapped(buf, page_size, first_bits)
        if not behs:
            return [], [], ['wrapped memory image has no block headers']
        data = behs[0].data
        bit = behs[0].sample_bit_address
        limit = page_size * 8
        if behs[0].block_count != 1:
            sample_time, event_time = _wrapped_start(
                behs[0], launch, delay, interval, first_bits, flag, opts)
            first_block_after_wrap = True
            if behs[0].interval and behs[0].interval != interval:
                interval = behs[0].interval
            sample_time -= timedelta(seconds=interval)
    else:
        data = buf
        bit = h.int(0x880D) * 8
        limit = h.int(0x881F, len(buf)) * 8

    samples, events = [], []
    n_samples = n_events = 0
    pending = None          # alarm-cleared event waiting for its data event
    burst_raw = {}          # physical channel -> raw value
    burst_done = False

    def last_block():
        return not behs or block == len(behs) - 1

    def eof(t):
        events.append(Event(t, 'End Of File', 15, 0, 0))

    while True:
        if bit + flag_width > limit:
            if last_block():
                eof(event_time)
                break
            block += 1
            data, bit = behs[block].data, behs[block].sample_bit_address
            first_block_after_wrap = False
            continue
        try:
            head = get_bits(data, bit, first_bits)
        except IndexError:
            eof(max(sample_time, event_time))
            break
        ev = None
        if head == flag:
            ev = parse_event(data, bit + first_bits, first_bits, flag)
            bit = ev.end_bit
            if ev.type == 14 and ev.subcode == 75:
                ev.valid = False
            if ev.type == 15:
                if last_block():
                    event_time += timedelta(seconds=ev.temporal_offset)
                    eof(event_time)
                    break
                block += 1
                data, bit = behs[block].data, behs[block].sample_bit_address
                first_block_after_wrap = False
                continue

        if burst_done:
            # HOBOware emits the burst point here, and drops whatever record
            # it has just read if that record was an event.
            _burst_sample(samples, used, by_key, burst_raw, event_time)
            n_samples += 1
            burst_raw.clear()
            burst_done = False
            continue

        if ev is not None:
            if (pending is not None and ev.type == 14
                    and ev.subcode == SUB_DATA_EVENT):
                pending.data, pending.data_len = ev.data, ev.data_len
                pending.complete = True
                ev, pending = pending, None
            if not ev.complete:
                pending = ev
                continue
            if ev.type == 14 and ev.subcode == 42:
                continue
            n_events += 1
            if bits_per_sample > 0 and ev.temporal_offset > interval:
                if ev.type == 15:
                    ev.temporal_offset = 0
                elif ev.type != 14 and ev.temporal_offset > interval + 1:
                    raise HoboFormatError(
                        f'{ev.name}: invalid interval offset '
                        f'{ev.temporal_offset} s')
            if n_samples == 0 and n_events == 1 and first_block_after_wrap:
                ev.temporal_offset = 0
            if (n_samples == 0 and n_events == 1 and ev.type == 14
                    and ev.subcode in (32, 33)):
                ev.temporal_offset = 0
            event_time += timedelta(seconds=ev.temporal_offset)

            if ev.type == 14:
                if ev.subcode == 1 and n_samples == 0 and n_events == 1:
                    # Started: the first sample follows one interval later
                    first_sample = event_time
                    sample_time = event_time - timedelta(seconds=interval)
                    ev.temporal_offset = 0
                elif ev.subcode == 16:
                    new = ev.data & 0xFFFFFFFF
                    sample_time = event_time
                    interval = new
                elif ev.subcode == 15:
                    if (ev.data >> 24) & 0xFF == 15:
                        if events and events[-1].subcode == 0:
                            events.pop()
                        ev.valid = False
                elif 53 <= ev.subcode <= 56:
                    burst_raw[ev.subcode - 53] = ev.data
                    ev.valid = False
                elif ev.subcode == 57:
                    burst_done = True
                    ev.valid = False
                elif ev.subcode in (52, 74):
                    ev.valid = False
            if ev.type not in range(6, 14) and ev.valid \
                    and ev.subcode not in (5, 6):
                events.append(Event(event_time, ev.name, ev.type,
                                    ev.subcode, ev.data))
            continue

        # sample
        if limit < bit + bits_per_sample:
            eof(max(sample_time, event_time))
            break
        if bits_per_sample <= 0:
            break
        sample_time += timedelta(seconds=interval)
        event_time = sample_time
        try:
            word = get_bits(data, bit, bits_per_sample)
        except IndexError:
            break
        raw = {}
        for c in reversed(stream):
            raw[c.key] = word & ((1 << c.bits) - 1)
            word >>= c.bits
        samples.append(Sample(sample_time,
                              _convert(used, by_key, raw)))
        n_samples += 1
        bit += bits_per_sample

    if not events or events[-1].type != 15:
        eof(max(sample_time, event_time))
    return samples, events, warnings


def _convert(used, by_key, raw):
    values = {}
    for c in used:
        if c.secondaries:
            if any(not by_key[k].enabled for k in c.secondaries):
                values[c.key] = -888.888
                continue
            values[c.key] = c.xfer([raw[k] for k in c.secondaries])
        elif c.real:
            values[c.key] = c.xfer([raw[c.key]])
    return values


def _burst_sample(samples, used, by_key, burst_raw, t):
    raw = {}
    for c in used:
        if c.physical in burst_raw:
            raw[c.key] = burst_raw[c.physical]
    values = {}
    for c in used:
        if c.key in raw and not c.secondaries:
            values[c.key] = c.xfer([raw[c.key]])
        elif c.secondaries and all(k in raw for k in c.secondaries):
            values[c.key] = c.xfer([raw[k] for k in c.secondaries])
    samples.append(Sample(t, values, burst=True))


def _wrapped_start(beh, launch, delay, interval, first_bits, flag, opts):
    """Sample/event clocks at the oldest surviving block of a wrapped
    logger (the non-CX branch of OLoggerUSBPhoenix.parseSensorData)."""
    if beh.interval and beh.interval != interval:
        interval = beh.interval
    ssl = beh.seconds_since_launch
    first_is_sample = get_bits(beh.data, beh.sample_bit_address,
                               first_bits) != flag
    if beh.seconds_since_launch_button_start:
        lead, button = beh.seconds_since_launch_button_start, True
    else:
        lead, button = delay, False
    sample = launch + timedelta(seconds=delay)
    if not first_is_sample:
        whole = int((ssl - lead) / interval)
        on_grid = (ssl - lead) % interval == 0
        if opts.burst:
            if beh.seconds_to_next_sample_burst == 0:
                ahead = whole * interval + interval
            else:
                ahead = (ssl - lead) + beh.seconds_to_next_sample_burst
        else:
            ahead = whole * interval + (0 if on_grid else interval)
            if button:
                ahead += lead
        sample += timedelta(seconds=ahead)
        event = launch + timedelta(seconds=ssl)
    else:
        sample = launch + timedelta(seconds=ssl)
        event = sample
    return sample, event


def read(path):
    with open(path, 'rb') as f:
        return decode(f.read())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def write_csv(hf, out):
    w = csv.writer(out)
    keys = [c.key for c in hf.channels]
    w.writerow(['#', f'Date Time, GMT{_fmt_tz(hf.launch_time)}'] +
               [f'{c.name} ({c.unit})' for c in hf.channels] + ['burst'])
    for i, s in enumerate(hf.samples, 1):
        w.writerow([i, s.time.strftime('%Y-%m-%d %H:%M:%S')] +
                   [s.values.get(k, '') for k in keys] +
                   ['1' if s.burst else ''])


def _fmt_tz(dt):
    off = dt.utcoffset() or timedelta()
    sign = '-' if off < timedelta() else '+'
    m = abs(int(off.total_seconds())) // 60
    return f'{sign}{m // 60:02d}:{m % 60:02d}'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('file')
    ap.add_argument('--csv', help='write samples to this CSV file')
    ap.add_argument('--header', action='store_true',
                    help='dump the header tags and exit')
    args = ap.parse_args(argv)
    with open(args.file, 'rb') as f:
        buf = f.read()
    if args.header:
        tags, end = parse_header(buf)
        dump_header(tags)
        print(f'(header ends at byte {end})')
        return 0
    hf = decode(buf)
    print(f'{hf.model} (PID {hf.pid}) serial {hf.serial}')
    print(f'launched {hf.launch_time.isoformat()}, interval {hf.interval} s')
    print('channels: ' + ', '.join(f'{c.name} [{c.unit}]'
                                   for c in hf.channels))
    print(f'{len(hf.samples)} samples, {len(hf.events)} events')
    for s in hf.samples[:3] + hf.samples[-3:]:
        print(f'  {s.time.isoformat()}  {s.values}')
    for e in hf.events:
        print(f'  {e.time.isoformat()}  {e.name}')
    for w in hf.warnings:
        print('warning:', w)
    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8') as out:
            write_csv(hf, out)
        print(f'wrote {args.csv}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
