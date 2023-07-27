import requests
import tempfile
import rrdtool
import pandas as pd
import numpy as np
import datetime
import gspread
import json
import os

def process_worksheet(worksheet):

  # Column J is the name of the entry in the factory
  cells = worksheet.get('J2:J')

  for idx, cell in enumerate(cells):
      if len(cell) == 0 or cell[0] == '':
          continue

      entry = cell[0]
      url = f'http://gfactory-2.opensciencegrid.org/factory/monitor/entry_{entry}/total/Status_Attributes.rrd'
      response = requests.get(url)
      if response.status_code != 200:
          print(f'Error {entry}: {response.status_code}')
          continue
      tmp_file = tempfile.NamedTemporaryFile(delete=False)
      tmp_file.write(response.content)
      tmp_file.close()

      # Now read in the rrd
      info = rrdtool.info(tmp_file.name)

      # Cores at collector are ClientCoresTotal
      # Requested idle glideins are ReqIdle
      result = rrdtool.fetch(tmp_file.name, "AVERAGE")

      date_range = pd.date_range(pd.to_datetime(result[0][0], unit='s', origin='unix'),
                                pd.to_datetime(
                                    result[0][1], unit='s', origin='unix'),
                                freq='5min')[:-1]

      df = pd.DataFrame(np.array(result[2]), columns=result[1],
                        index=date_range)

      # Treat nan as 0
      df = df.fillna(0)

      # Get the average of the last 4 hours for the ClientCoresTotal column
      # RRD is every 5 minutes, so 12 entries an hour, 48 hours is 576 entries
      client_cores_avg = df['ClientCoresTotal'].tail(576).mean()
      req_idle_avg = df['ReqIdle'].tail(576).mean()

      print(f'Entry {entry} has an average of {client_cores_avg} client cores and {req_idle_avg} requested idle glideins.')

      # Ok, now we have the averages, now for some logic
      new_value = 'Unknown'
      if client_cores_avg > 1:
          new_value = 'Production'
          # print(f'Entry {entry} is production')
      elif req_idle_avg < 1:
          new_value = 'No pressure'
          # print(f'Entry {entry} is no pressure')
      elif client_cores_avg < 1 and req_idle_avg > 1:
          new_value = 'Broken'
          # print(f'Entry {entry} is broken')

      # Changable values
      changeable_values = ['Production', 'Broken', 'No pressure']
      # Get the current value
      status_cell = f'C{idx + 2}'
      current_value = worksheet.acell(status_cell).value
      # print(current_value)
      if current_value not in changeable_values:
          print(f'Entry {entry} has an invalid value of {current_value}')
          continue
      if current_value == new_value:
          print(f'Entry {entry} has not changed')
        #   continue
      # Ok, we need to change the value
      print(f'Changing entry {entry} status from {current_value} to {new_value}')
      worksheet.update_acell(status_cell, new_value)
      print(f'Changing entry {entry} last status to {current_value}')
      last_status_cell = f'D{idx + 2}'
      worksheet.update_acell(last_status_cell, current_value)


def main():

    # Authenticate with Google
    service_account = {
        'type': os.environ['TYPE'],
        'project_id': os.environ["PROJECT_ID"],
        'private_key_id': os.environ['PRIVATE_KEY_ID'],
        'private_key': os.environ['PRIVATE_KEY'].replace('\\n', '\n'), # Need to do because os.environ escapes the slash
        'client_email': os.environ['CLIENT_EMAIL'],
        'client_id': os.environ['CLIENT_ID'],
        'auth_uri': os.environ['AUTH_URI'],
        'token_uri': os.environ['TOKEN_URI'],
        'auth_provider_x509_cert_url': os.environ['AUTH_PROVIDER_X509_CERT_URL'],
        'client_x509_cert_url': os.environ['CLIENT_X509_CERT_URL'],
        'universe_domain': os.environ['UNIVERSE_DOMAIN']
    }
    gc = gspread.service_account_from_dict(service_account)

    # Open the sheet for this week
    # Calculate the date of the next monday
    today = datetime.date.today()
    next_monday = today + datetime.timedelta(days=7-today.weekday())

    # Format the date
    next_monday_fmt = next_monday.strftime('%Y-%m-%d')

    # Open the worksheet
    # TODO: come up with better way to open sheets. Would like to create a copy of the latest sheet, and get the id off of the newly created sheet and open here.
    worksheet = gc.open_by_key('1zowqPYTpB6YGi9at8GZpRaxGx4TCfkO7Hj_2TF1ZuCY').sheet1

    # Process the worksheet
    process_worksheet(worksheet)


if __name__ == "__main__":
    main()
