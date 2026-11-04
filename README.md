# Metra Train Tracker for Home Assistant

See upcoming [Metra](https://metra.com) trains, delays and service alerts for your
stations in Home Assistant, with a dashboard card included.

This is an unofficial community project. It isn't affiliated with or endorsed by Metra.

## What you get

For each station you add:

| Entity | Description |
| --- | --- |
| Next arrival | When the next train is expected, including any delay |
| Next train | The next train's number |
| Next train delay | Minutes late, or negative if early |
| Service alert | On when the line has a service alert, with the text as an attribute |

You also get the **Metra Train Tracker** dashboard card, with no separate install.

## Requirements

- Home Assistant 2026.10 or newer
- A Metra GTFS API key, which you can request from
  [Metra's developer page](https://metra.com/developers)

## Install

### HACS

1. In HACS, open the ⋮ menu and choose **Custom repositories**.
2. Add `https://github.com/adamjeffery/metra-ha` with the type **Integration**.
3. Download **Metra Train Tracker**, then restart Home Assistant.

### Manual

Copy `custom_components/metra_train_tracker` into the `custom_components` folder next to your
`configuration.yaml`, then restart Home Assistant.

## Set up

1. Go to **Settings → Devices & services → Add integration**, choose **Metra Train Tracker** and
   enter your API key.
2. On the Metra Train Tracker integration page, choose **Add station**, then pick:
   - **Line** and **Station**
   - **Direction**: inbound, outbound or both
   - **Trains to show**: 1 to 10
   - **Alert terms to ignore**: hides alerts containing these words, such as
     `construction`
3. Repeat for each station and direction you want.

To change a station later, use the ⋮ menu next to it on the integration page.

## Add the card

Edit a dashboard, add a card and search for **Metra Train Tracker**. You can also add it
with YAML:

```yaml
type: custom:train-tracker-card
entity: sensor.up_w_lombard_inbound_next_arrival
time_format: 12h
```

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `entity` | Yes | | The station's **Next arrival** sensor |
| `time_format` | No | `12h` | `12h` (9:04 AM), `24h` (09:04) or `raw` (09:04:00) |

## Example automation

Get a notification when your train is running late:

```yaml
alias: Train running late
triggers:
  - trigger: numeric_state
    entity_id: sensor.up_w_lombard_inbound_next_train_delay
    above: 5
actions:
  - action: notify.notify
    data:
      message: >-
        Train {{ states('sensor.up_w_lombard_inbound_next_train') }} is
        {{ trigger.to_state.state }} minutes late.
```

## How it works

- **Schedule:** downloaded from Metra at startup and daily at 5 AM Central. A copy is
  kept in `.storage`, so restarts don't download it again. If a download fails, the
  previous schedule stays in use and the download is retried hourly.
- **Realtime data:** checked every minute. Metra is only called every 2 minutes for
  delays and positions, and every 5 minutes for alerts. A copy up to an hour old is used
  during an outage, after which the entities become unavailable.
- **Upcoming trains:** only the rest of the current service day is shown, so after the
  last train the list is empty until the next day's service begins.

## Troubleshooting

| Problem | What to try |
| --- | --- |
| A repair asks you to re-authenticate | Metra rejected your API key. Enter a new one. |
| Entities are unavailable | Metra's realtime feed has been unreachable for over an hour. Check the logs. |
| The card says `Stale: data from…` | The data is over 5 minutes old, usually because Home Assistant can't reach Metra. |
| Custom element doesn't exist | Hard-refresh your browser (Ctrl+Shift+R). |

## Development

```bash
pip install -r requirements_test.txt
pytest
```
