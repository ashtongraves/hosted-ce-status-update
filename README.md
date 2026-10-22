Hosted CE Status Updater
========================

This script udpates the Hosted-CE spreadsheet with current status.

## Behavior

The script queries the factory for the RRD files and extracts the values.  It will update the status field if the status field is currently `Production`, `Broken`, or `No Pressure`.  If the status is any other value, it will not update the status.

To determine the current status of the endpoint, it calculates over the previous 4 hours:

- Number of cores registered on the client (frontend)
- Number of requested glideins at the entry

The logic is then:

1. **Production**: If average number of cores at the client is above 1
2. **No Pressure**: If the requested number of glideins is less than 1
3. **Broken**: If the average number of cores at the client is below 1, and the requested number of glideins is above 1

## Topology Downtimes

Before checking the factory, the script reads the current downtimes from [topology](https://topology.opensciencegrid.org/rgdowntime/xml) and matches them against the hostname column.

- If a CE is in downtime and its status is one the script manages, the status is set to **Maintenance** and a note is left on the status cell.
- When that downtime ends, the script sees the note, sets the status back to **Production**, and removes the note.
- A **Maintenance** status set by hand (no note) is never changed by the script, with or without a downtime.
- If topology can't be reached, downtime checks are skipped for that run.


## Credentials

It uses gspread to process the spreadsheet.  Read the gspread credential manual: https://docs.gspread.org/en/v5.4.0/oauth2.html