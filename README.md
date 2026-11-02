# Vantage InFusion for Home Assistant

Custom Home Assistant integration for Vantage InFusion controllers. It connects to the controller, creates lights, switches, covers, and thermostats, and puts each load in the Vantage area it belongs to.

Report problems at [SmarterHomeApp/home-assistant-vantage/issues](https://github.com/SmarterHomeApp/home-assistant-vantage/issues).

## Install with HACS

[HACS](https://www.hacs.xyz/docs/use/) has to be installed in Home Assistant before these steps.

1. Open Home Assistant.
2. In the left sidebar, open **HACS**.
3. Click the three-dot menu in the top right.
4. Choose **Custom repositories**.
5. In **Repository**, paste:

   ```text
   https://github.com/SmarterHomeApp/home-assistant-vantage
   ```

6. Set **Category** to **Integration**.
7. Click **Add**. If Home Assistant says the repository already exists, close the dialog. It is already available.
8. Search HACS for **Vantage InFusion**.
9. Open it and click **Download**.
10. Restart Home Assistant when HACS asks you to. You can also restart from **Settings → System → Restart**.

## Add the controller

1. Go to **Settings → Devices & services**.
2. Click **Add integration** in the bottom right.
3. Search for **Vantage** and select **Vantage InFusion**.
4. Fill in the form:
   - **Controller IP address:** leave this blank to scan the local network shown on the form, or type the controller IP.
   - **Local network:** filled in from this Home Assistant machine. It cannot be edited.
   - **Username** and **Password:** leave blank unless the controller requires a login.
   - **Use persistent last-known-good fallback cache:** leave this on. Home Assistant can start from the last successful download if the controller is briefly unreachable.
   - **Comma-separated VIDs to omit:** optional. Use this to skip specific loads.
   - **VID range (min-max):** leave the default `0-999999999` unless you want this entry to cover only part of the controller.
   - **Force SSL ports:** leave this off. The integration uses the encrypted ports when the plain ports refuse the connection.
5. Click **Submit**.

Home Assistant then adds the loads. Each one keeps its own name, such as LAMP or CEILING, and is placed in its Vantage area, such as Jack's Office or Reception. Open **Settings → Areas** to see them.

If submit fails with "Could not connect", check that Home Assistant can reach the controller on port 3001 or 3010, then try again with the controller IP filled in.

## Install without HACS

Copy the `custom_components/vantage` folder into your Home Assistant config directory, so the path is:

```text
<config>/custom_components/vantage
```

On Home Assistant OS, the config directory is `/config`. Restart Home Assistant, then follow **Add the controller** above.

## What it does

- Downloads the controller configuration on startup
- Scans this machine's local network on ports 3001 and 3010 when the host is left blank
- Keeps a last-known-good configuration cache
- Filters loads by VID range and by an omit list
- Creates lights, switches, covers, and thermostats
- Reads the current level of each load before the entities are created
- Listens for later status changes from the controller
- Creates a Home Assistant area for each Vantage area and puts the load in it
