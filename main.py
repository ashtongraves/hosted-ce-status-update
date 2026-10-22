import requests
import tempfile
import rrdtool
import pandas as pd
import numpy as np
import datetime
import gspread
import json
import os
import time
import xml.etree.ElementTree as ET
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import GoogleDriveFile

SERVICE_ACCOUNT = {
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

# Statuses the script is allowed to overwrite, anything else was set by hand
AUTOMATED_STATUSES = ['Production', 'Broken', 'No pressure', 'Blocked']

DOWNTIME_URL = 'https://topology.opensciencegrid.org/rgdowntime/xml'
# Note left on the status cell so we know the Maintenance status came from topology and not a person
DOWNTIME_NOTE = 'Set to Maintenance by update script because of a topology downtime'

def get_downtime_hosts():
  # Returns the set of CE hostnames currently in downtime, or None if topology couldn't be read
  try:
    response = requests.get(DOWNTIME_URL, timeout=60)
    response.raise_for_status()
    root = ET.fromstring(response.content)
  except (requests.RequestException, ET.ParseError) as e:
    print(f'Unable to get downtimes from topology, skipping downtime checks: {e}')
    return None

  hosts = set()
  for downtime in root.findall('./CurrentDowntimes/Downtime'):
    services = [service.findtext('Name') for service in downtime.findall('./Services/Service')]
    if 'CE' not in services:
      continue
    hosts.add((downtime.findtext('ResourceFQDN') or '').strip().lower())
  return hosts

def process_worksheet(old_worksheet_id, new_worksheet_id):
  gc = gspread.service_account_from_dict(SERVICE_ACCOUNT)
  
  print('Updating old spreadsheet')
  old_worksheet = gc.open_by_key(old_worksheet_id).sheet1
  old_worksheet.update_acell('A1', 'ARCHIVED VERSION - DO NOT EDIT')

  worksheet = gc.open_by_key(new_worksheet_id).sheet1
  worksheet.update_acell('B1', f'Update Script Ran {datetime.datetime.now()}')

  downtime_hosts = get_downtime_hosts()

  # Column B is the CE hostname, column J is the name of the entry in the factory
  rows = worksheet.get('B2:J')

  s = requests.Session()
  adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1)
  s.mount('http://', adapter)

  for idx, row in enumerate(rows):
    if len(row) < 9 or row[8].strip() == '':
        continue

    hostname = row[0].strip().lower()
    entry = row[8].strip()
    status_cell = f'C{idx + 2}'
    last_status_cell = f'D{idx + 2}'

    if downtime_hosts is not None:
      if hostname in downtime_hosts:
        current_value = worksheet.acell(status_cell).value
        if current_value in AUTOMATED_STATUSES:
          print(f'Entry {entry} is in topology downtime, changing status from {current_value} to Maintenance')
          worksheet.update_acell(status_cell, 'Maintenance')
          worksheet.update_acell(last_status_cell, current_value)
          worksheet.update_note(status_cell, DOWNTIME_NOTE)
        else:
          print(f'Entry {entry} is in topology downtime, leaving status as {current_value}')
        time.sleep(10)
        continue

      if worksheet.get_note(status_cell) == DOWNTIME_NOTE:
        # The downtime we set Maintenance for has ended
        worksheet.clear_note(status_cell)
        current_value = worksheet.acell(status_cell).value
        if current_value == 'Maintenance':
          print(f'Entry {entry} topology downtime has ended, changing status from Maintenance to Production')
          worksheet.update_acell(status_cell, 'Production')
          worksheet.update_acell(last_status_cell, current_value)
          time.sleep(10)
          continue

    url = f'http://gfactory-1.osg-htc.org/factory/monitor/entry_{entry}/total/Status_Attributes.rrd'
    ferm_url = f'http://fermifactory02.fnal.gov:8319/factory/monitor/entry_{entry}/total/Status_Attributes.rrd'
    response = s.get(url)
    if response.status_code != 200:
      response = s.get(ferm_url)
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

    date_range = pd.date_range(pd.to_datetime(result[0][0], unit='s', origin='unix'), pd.to_datetime(result[0][1], unit='s', origin='unix'), freq='5min')[:-1]

    df = pd.DataFrame(np.array(result[2]), columns=result[1], index=date_range)
    df = df.fillna(0)

    # RRD is every 5 minutes, so 12 entries an hour, 48 hours is 576 entries
    time_period_hours = 48
    total_entries = int((60 / 5) * time_period_hours)

    client_cores_avg = df['ClientCoresTotal'].tail(total_entries).mean()
    req_idle_avg = df['ReqIdle'].tail(total_entries).mean()
    idle_glidein_job_avg = df['StatusIdle'].tail(total_entries).mean()
    running_glidein_cores_avg = df['StatusRunningCores'].tail(total_entries).mean() 

    print(f'Entry {entry}: [Client Core Avg: {client_cores_avg}, Requested Idle Gliden Avg: {req_idle_avg}, Idle Glidein Job Avg: {idle_glidein_job_avg}, Running Glidein Cores Avg: {running_glidein_cores_avg}]')

    new_value = 'Unknown'
    if req_idle_avg > 0:
      if running_glidein_cores_avg == 0:
        new_value = 'Blocked'
      else:
        if client_cores_avg == 0:
          new_value = 'Broken'
        else:
          new_value = 'Production'
    else:
      new_value = 'No pressure'

    current_value = worksheet.acell(status_cell).value

    if current_value not in AUTOMATED_STATUSES:
      print(f'Entry {entry} has an invalid value of {current_value}')
    else:
      if current_value == new_value:
        print(f'Entry {entry} has not changed')

      print(f'Changing entry {entry} status from {current_value} to {new_value}')
      worksheet.update_acell(status_cell, new_value)

      print(f'Changing entry {entry} last status to {current_value}')
      worksheet.update_acell(last_status_cell, current_value)

    # We're sleeping so we don't overload the api and get rate limited
    time.sleep(10)

def create_worksheet():
  gauth = GoogleAuth(settings= {
    'client_config_backend': 'service',
    'service_config': {
        'client_json_dict': SERVICE_ACCOUNT
    }
  })
  gauth.ServiceAuth()
  drive = GoogleDrive(gauth)

  today = datetime.date.today()
  next_monday = today + datetime.timedelta(days=7-today.weekday())

  old_spreadsheet_id = None
  old_spreadsheet = None
  new_spreadsheet_id = None
  new_folder = None

  TEAM_DRIVE_ID = '0ABb7rkiXRlOpUk9PVA'
  HOSTED_CE_FOLDER_ID = '1-7ae75NshCbznXywFduBPeHDhaTRiMDO'
  hosted_ce_parent = drive.ListFile({'q':f"'{HOSTED_CE_FOLDER_ID}' in parents and trashed=false", 'corpora': 'teamDrive', 'teamDriveId': TEAM_DRIVE_ID, 'includeTeamDriveItems': True, 'supportsTeamDrives': True}).GetList()

  for drive_item in hosted_ce_parent:
    if f'{today.year} CE Status Weekly Reports' in drive_item['title']:
      old_folder_items = drive.ListFile({'q':f"'{drive_item['id']}' in parents and trashed=false", 'corpora': 'teamDrive', 'teamDriveId': TEAM_DRIVE_ID, 'includeTeamDriveItems': True, 'supportsTeamDrives': True}).GetList()
  for old_folder_item in old_folder_items:
    if f'OSPool CE Status - {today.strftime("%Y-%m-%d")}' in old_folder_item['title']:
      old_spreadsheet_id = old_folder_item['id']
      old_spreadsheet = GoogleDriveFile(auth=gauth, metadata=old_folder_item, uploaded=True)
  
  for drive_item in hosted_ce_parent:
    if f'{next_monday.year} CE Status Weekly Reports' in drive_item['title']:
      new_folder_items = drive.ListFile({'q':f"'{drive_item['id']}' in parents and trashed=false", 'corpora': 'teamDrive', 'teamDriveId': TEAM_DRIVE_ID, 'includeTeamDriveItems': True, 'supportsTeamDrives': True}).GetList()
      new_folder = GoogleDriveFile(auth=gauth, metadata=drive_item, uploaded=True)
  for new_folder_item in new_folder_items:
    if f'OSPool CE Status - {next_monday.strftime("%Y-%m-%d")}' in new_folder_item['title']:
      new_spreadsheet_id = new_folder_item['id'] 

  new_spreadsheet_id = new_spreadsheet_id or old_spreadsheet.Copy(target_folder=new_folder, new_title=f'OSPool CE Status - {next_monday.strftime("%Y-%m-%d")}').get('id')
  return old_spreadsheet_id, new_spreadsheet_id

def main():
  old_spreadsheet_id, new_spreadsheet_id = create_worksheet()
  process_worksheet(old_spreadsheet_id, new_spreadsheet_id)

if __name__ == "__main__":
  main()
