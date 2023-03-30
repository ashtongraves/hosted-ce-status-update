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


## Credentials

It uses gspread to process the spreadsheet.  Read the gspread credential manual: https://docs.gspread.org/en/v5.4.0/oauth2.html