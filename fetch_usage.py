from unifi import UnifiPy
from datetime import datetime, timedelta


# https://domain:8443
CONTROLLER = ""

# Controller Username, we created a seperate user for this but you dont need to.
USERNAME = ""
PASSWORD = ""

start = datetime.now() - timedelta(days=7)
end = datetime.now()
epoch = datetime.utcfromtimestamp(0)


# Datetime to epoch milliseconds
def unix_time_millis(dt):
    return int((dt - epoch).total_seconds() * 1000.0)


def main():
    unifipy = UnifiPy(
        controller=CONTROLLER,
        username=USERNAME,
        password=PASSWORD
    )

    # Fetch all APs from the Unifi Controller, Site name is optional. If not provided, devices from all sites will be returned.
    devices = unifipy.devices.get(site_name="aoewakfu")

    devices_by_mac = {device.mac: device for device in devices}

    daily_usage = unifipy.usage.get_daily_usage(
        site="aoewakfu",
        start=unix_time_millis(start),
        end=unix_time_millis(end),
        devices=list(devices_by_mac.keys()) # Usage is fetched from the Unifi Controller using mac addresses.
    )

    for x in daily_usage:
        utc_time = datetime(1970, 1, 1) + timedelta(milliseconds=x["time"])
        download = x.get("tx_bytes")/1000000/1000
        upload = x.get("rx_bytes")/1000000/1000
        ap = x.get("ap")

        device = devices_by_mac.get(ap)
        
        print(utc_time, ap, device.name, download, upload)


    hourly = unifipy.usage.get_hourly_usage(
        site="aoewakfu",
        start=unix_time_millis(start),
        end=unix_time_millis(end),
        devices=list(devices_by_mac.keys()) # Usage is fetched from the Unifi Controller using mac addresses.
    )

    for x in hourly:
        utc_time = datetime(1970, 1, 1) + timedelta(milliseconds=x["time"])
        download = x.get("tx_bytes")/1000000/1000
        upload = x.get("rx_bytes")/1000000/1000
        ap = x.get("ap")
        
        device = devices_by_mac.get(ap)
        
        print(utc_time, ap, device.name, download, upload)



if __name__ == "__main__":
    main()
