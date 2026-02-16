"""Constants for the Dante Audio Network integration."""
import logging

DOMAIN = "dante"
LOGGER = logging.getLogger(__package__)

SCAN_INTERVAL = 30
MDNS_TIMEOUT = 5.0
DEVICE_MISS_LIMIT = 10  # drop device after this many consecutive missed discovery cycles (~5 min)

PLATFORMS = ["sensor", "select", "number", "switch", "button"]

SAMPLE_RATES = [44100, 48000, 88200, 96000, 176400, 192000]
SAMPLE_RATE_LABELS = {
    44100: "44.1 kHz",
    48000: "48 kHz",
    88200: "88.2 kHz",
    96000: "96 kHz",
    176400: "176.4 kHz",
    192000: "192 kHz",
}

ENCODINGS = [16, 24, 32]
ENCODING_LABELS = {16: "PCM 16-bit", 24: "PCM 24-bit", 32: "PCM 32-bit"}

GAIN_LABELS_INPUT = {
    1: "+24 dBu",
    2: "+4 dBu",
    3: "+0 dBu",
    4: "0 dBV",
    5: "-10 dBV",
}

GAIN_LABELS_OUTPUT = {
    1: "+18 dBu",
    2: "+4 dBu",
    3: "+0 dBu",
    4: "0 dBV",
    5: "-10 dBV",
}

AVIO_INPUT_MODELS = ["DAI1", "DAI2"]
AVIO_OUTPUT_MODELS = ["DAO1", "DAO2"]

SUBSCRIPTION_NONE = "None"

SAP_MULTICAST = "239.255.255.255"
SAP_PORT = 9875
SAP_TIMEOUT = 10.0  # seconds to listen for SAP announcements
