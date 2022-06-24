from multiprocessing import Pool
import requests
import json
from datetime import datetime
from retrying import retry


UNIFI_STATE_CODES = {
    0: "Disconnected",
    1: "Connected",
    2: "Pending",
    3: "Firmware Mismatch",
    4: "Upgrading",
    5: "Provisioning",
    6: "Heartbeat Missed",
    7: "Adopting",
    8: "Deleting",
    9: "Inform Error",
    10: "Adopting Failed",
    11: "Isolated",
    9999: "Rebooting"
}


class UnifiGetException(Exception):
    pass


class UnifiPostException(Exception):
    pass


class UnifiFetchException(Exception):
    pass


def rssi_to_connection_percent(rssi):
    """
    From Unifi Controller Source Code
    if (e = parseFloat(e)){
        e = ((e = Math.min(45, Math.max(e, 5))) - 5) / 40 * 99
        if (e === 0}{
            return "0"
        }
        return e.toPrecision(2)) + "%" 
    } else {
        return ""
    }
    """
    return round((min(45, max(rssi, 5)) - 5) / 40 * 99, 2)


def is_device_online(device):
    if device.get("state") == 1:
        return True
    return False


class UnifiDevice:
    def __init__(self, device_dict):
        self.id = device_dict.get("_id")
        self.mac = device_dict.get("mac")
        self.ip = device_dict.get("ip")
        self.model = device_dict.get("model")
        self.type = device_dict.get("type")
        self.version = device_dict.get("version")
        self.adopted = device_dict.get("adopted")
        self.site_id = device_dict.get("site_id")
        self.inform_url = device_dict.get("inform_url")
        self.name = device_dict.get("name")
        self.mesh_sta_vap_enabled = device_dict.get("mesh_sta_vap_enabled")
        self.state = device_dict.get("state")
        self.vwireEnabled = device_dict.get("vwireEnabled")
        if device_dict.get("uplink"):
            self.uplink_mac = device_dict.get("uplink").get("uplink_mac")
            self.uplink_type = device_dict.get("uplink").get("type")
            self.uplink_speed = device_dict.get("uplink").get("speed")
            self.uplink_rssi = device_dict.get("uplink").get("rssi")
            self.uplink_signal = device_dict.get("uplink").get("signal")
            self.uplink_noise = device_dict.get("uplink").get("noise")
            self.uplink_rx_rate = device_dict.get("uplink").get("rx_rate")
            self.uplink_tx_rate = device_dict.get("uplink").get("tx_rate")
        else:
            self.uplink_mac = None
            self.uplink_type = None
            self.uplink_speed = None
            self.uplink_rssi = None
            self.uplink_signal = None
            self.uplink_noise = None
            self.uplink_rx_rate = None
            self.uplink_tx_rate = None

        self.uplink_table = device_dict.get("uplink_table") if device_dict.get("uplink_table") else None
        self.uplink_1 = device_dict.get("mesh_uplink_1") if device_dict.get("mesh_uplink_1") else None
        self.uplink_2 = device_dict.get("mesh_uplink_2") if device_dict.get("mesh_uplink_2") else None
        self.scanning = device_dict.get("spectrum_scanning") if device_dict.get("spectrum_scanning") else False
        self.last_seen = datetime.fromtimestamp(device_dict.get("last_seen")) if device_dict.get("last_seen") else None
        self.uptime = device_dict.get("uptime")
    
        self.connect_request_ip = device_dict.get("connect_request_ip")
        self.gateway_mac = device_dict.get("gateway_mac")if device_dict.get("gateway_mac") else None

    def is_online(self):
        if self.state == 1:
            return True
        return False


class UnifiSite:
    def __init__(self, device_dict):
        self.id = device_dict.get("_id")
        self.name = device_dict.get("name")
        self.desc = device_dict.get("desc")


class UnifiSiteApi:
    ENDPOINT = "/api/self/sites"

    def __init__(self, unifipy):
        self.unifipy = unifipy

    def get(self):
        sites = self.unifipy.get(self.ENDPOINT)["data"]
        return [UnifiSite(site) for site in sites]


class UnifiUsageApi:
    ENDPOINT = "/api/s/%s/stat/report/%s.ap"

    def __init__(self, unifipy):
        self.unifipy = unifipy
    
    def get_hourly_usage(self, site, start, end, devices):
        url = self.ENDPOINT % (site, "hourly")
        data = {"attrs": ["tx_bytes", "rx_bytes", "time"], "end": end, "start": start, "macs": devices}
        usage = self.unifipy.post(url, data=data)["data"]
        return usage

    def get_daily_usage(self, site, start, end, devices):
        url = self.ENDPOINT % (site, "daily")
        data = {"attrs": ["tx_bytes", "rx_bytes", "time"], "end": end, "start": start, "macs": devices}
        usage = self.unifipy.post(url, data=data)["data"]
        return usage


class UnifiDeviceApi:
    ENDPOINT = "/api/s/%s/stat/device"
    CMD_ENDPOINT = "/api/s/%s/cmd/devmgr"
    UPDATE_ENDPOINT = "/api/s/%s/rest/device/%s"
    SITE_CMD_ENDPOINT = "/api/s/%s/cmd/sitemgr"

    def __init__(self, unifipy):
        self.unifipy = unifipy

    @retry(stop_max_attempt_number=3, wait_random_min=1000, wait_random_max=2000)
    def get(self, site_name=None):
        """
            Fetches devices from the Unifi controller.     
            :param site_name: optional, if defined only devices from this site will be returned

        """
        devices = []
        if site_name:
            devices += self.unifipy.get(self.ENDPOINT % site_name)["data"]
            return[UnifiDevice(device) for device in devices]
        else:
            sites = self.unifipy.sites.get()
            site_names = [site.name for site in sites]
            pool = Pool(8)
            device_lists = pool.map(self.get, site_names)
            for x in device_lists:
                devices += x
        return devices


    def restart(self, mac, site_name):
        data = {
            "mac": mac,
            "reboot_type": "soft",
            "cmd": "restart"
        }
        try:
            r = self.unifipy.post(self.CMD_ENDPOINT % site_name, data)
            if "meta" in r and "rc" in r["meta"] and r["meta"]["rc"] == "ok":
                return True, ""
        except UnifiPostException:
            return False, "Problem communicating with the controller"
        return False, "Unknown Error"


    def set_uplinks(self, mac, site_name, prefer1, prefer2):
        data = {
            "mac": mac,
            "cmd": "set-priority-uplink",
            "prefer1": prefer1,
            "prefer2": prefer2
        }
        try:
            r = self.unifipy.post(self.CMD_ENDPOINT % site_name, data)
            if "meta" in r and "rc" in r["meta"] and r["meta"]["rc"] == "ok":
                return True, ""
        except UnifiPostException:
            return False, "Problem communicating with the controller"
        return False, "Unknown Error"


    def scan(self, mac, site_name):
        data = {
            "cmd": "spectrum-scan",
            "mac": mac
        }
        try:
            r = self.unifipy.post(self.CMD_ENDPOINT % site_name, data)
            if "meta" in r and "rc" in r["meta"] and r["meta"]["rc"] == "ok":
                return True, ""
        except UnifiPostException:
            return False, "Problem communicating with the controller"
        return False, "Unknown Error"


    def update(self, site_name, device_id, data):
        url = self.UPDATE_ENDPOINT % (site_name, device_id)
        try:
            r = self.unifipy.put(
                url, 
                data
            )
            if r.get("meta").get("rc") == "ok":
                return True, ""
        except UnifiPostException:
            return False, "Problem communicating with the controller"
        return False, "Unknown Error"


    def move(self, mac, site_id, site_name):
        data = {
            "mac": mac,
            "site": site_id,
            "cmd": "move-device"
        }
        try:
            r = self.unifipy.post(
                self.SITE_CMD_ENDPOINT % site_name, 
                data
            )
            if "meta" in r and "rc" in r["meta"] and r["meta"]["rc"] == "ok":
                return True, ""
        except UnifiPostException:
            return False, "Problem communicating with the controller"
        return False, "Unknown Error"

    def upgrade_firmware(self, mac, site_name, firmware_version):
        data = {
            "mac": mac,
            "upgrade_to_firmware": firmware_version,
            "cmd": "upgrade"
        }
        try:
            r = self.unifipy.post(self.CMD_ENDPOINT % site_name, data)
            if "meta" in r and "rc" in r["meta"] and r["meta"]["rc"] == "ok":
                return True, ""
        except UnifiPostException:
            return False, "Problem communicating with the controller"
        return False, "Unknown Error"


class UnifiPy:
    ENDPOINT_LOGIN = "/api/login"
    ENDPOINT_DEVICE = "/api/s/%s/stat/device"
    ENDPOINT = "/api/self/sites"
    session = None

    def __init__(self, controller, username, password):
        self.username = username
        self.password = password
        self.controller = controller
        self.session = requests.Session()
        self.login()

        self.sites = UnifiSiteApi(self)
        self.devices = UnifiDeviceApi(self)
        self.usage = UnifiUsageApi(self)


    def get(self, endpoint):
        r = self.session.get(self.controller + endpoint, timeout=10)
        if r.status_code != 200:
            raise UnifiGetException
        try:
            j = r.json()
        except json.decoder.JSONDecodeError:
            raise UnifiFetchException
        return j


    def post(self, endpoint, data):
        r = self.session.post(
            self.controller + endpoint,
            json=data, 
            timeout=10
        )
        if r.status_code != 200:
            raise UnifiPostException
        try:
            j = r.json()
        except json.decoder.JSONDecodeError:
            raise UnifiFetchException
        return j


    def post_no_json_response(self, endpoint, data):
        r = self.session.post(
            self.controller + endpoint, 
            json=data, 
            timeout=10
        )
        if r.status_code != 200:
            raise UnifiPostException


    def put(self, endpoint, data):
        r = self.session.put(self.controller + endpoint, json=data, timeout=10)
        if r.status_code != 200:
            raise UnifiPostException
        try:
            j = r.json()
        except json.decoder.JSONDecodeError:
            raise UnifiFetchException
        return j


    @retry(stop_max_attempt_number=3, wait_random_min=1000, wait_random_max=2000)
    def login(self):
        data = {
            "username": "%s" % self.username,
            "password": "%s" % self.password,
            "strict": True
        }
        r = self.post(self.ENDPOINT_LOGIN, data)
        self.session.headers["X-Csrf-Token"] = self.session.cookies["csrf_token"]


    def get_devices(self, site_code):
        j = self.get(self.ENDPOINT_DEVICE % (site_code,))
        devices = j.get("data")
        return devices


    def get_sites(self):
        j = self.get(self.ENDPOINT)
        sites = j.get("data")
        return sites


    def set_device_alias(self, site_id, device_id, alias):
        data = {"name": alias}
        endpoint = self.controller + "/api/s/%s/rest/device/%s" % (site_id, device_id)
        response = self.put(endpoint, data)
        return response


    def set_radio_config(self, site_id, device_id, config):
        endpoint = self.controller + "/api/s/%s/rest/device/%s" % (site_id, device_id)
        response = self.session.put(endpoint, json=config, timeout=5)
        return response


    def set_band_steering_mode(self, site_id, device_id, band_steering_setting=None):
        data = None
        endpoint = "/api/s/%s/rest/device/%s" % (site_id, device_id)
        if band_steering_setting == "prefer_5g":
            data = {"bandsteering_mode": "prefer_5g"}
        elif band_steering_setting == "balanced":
            data = {"bandsteering_mode": "equal"}
        elif band_steering_setting is None:
            data = {"bandsteering_mode": "equal"}
        if data is not None:
            response = self.session.put(
                self.controller + endpoint, json=data, timeout=5
            )
            return response


    def set_meshing(self, site_id, device_id, mesh):
        data = {"mesh_sta_vap_enabled": mesh}
        endpoint = self.controller + \
            "/api/s/%s/rest/device/%s" % (site_id, device_id)
        response = self.session.put(endpoint, json=data, timeout=5)
        return response


    def get_admins(self):
        endpoint = "/api/stat/admin"
        response = self.session.get(endpoint, timeout=5)
        return response


    def remove_admin(self, admin_id, site_name, remove=False):
        # Remove defaults to False to stop accidental user deletions.
        if admin_id:
            data = {"admin": admin_id, "cmd": "revoke-admin"}
            endpoint = "/api/s/%s/cmd/sitemgr" % (site_name,)
            if remove:
                response = self.post(endpoint, data)
                return response


    def set_perms(self, site_name, data):
        endpoint = "/api/s/%s/cmd/sitemgr" % (site_name,)
        response = self.post(endpoint, data)
        return response


    def sitemgr_post(self, site_name, data):
        endpoint = "/api/s/%s/cmd/sitemgr" % (site_name,)
        response = self.post(endpoint, data)
        return response
