import types

import pytest
import requests

import main

DOWNTIME_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<Downtimes>
  <PastDowntimes>
    <Downtime>
      <ResourceName>PAST-CE1</ResourceName>
      <ResourceFQDN>past-ce1.example.org</ResourceFQDN>
      <Services><Service><Name>CE</Name></Service></Services>
    </Downtime>
  </PastDowntimes>
  <CurrentDowntimes>
    <Downtime>
      <ResourceName>DOWN-CE1</ResourceName>
      <ResourceFQDN> Down-CE1.example.org </ResourceFQDN>
      <Services><Service><Name>CE</Name></Service></Services>
    </Downtime>
    <Downtime>
      <ResourceName>MULTI-CE1</ResourceName>
      <ResourceFQDN>multi-ce1.example.org</ResourceFQDN>
      <Services>
        <Service><Name>Squid</Name></Service>
        <Service><Name>CE</Name></Service>
      </Services>
    </Downtime>
    <Downtime>
      <ResourceName>CACHE1</ResourceName>
      <ResourceFQDN>cache1.example.org</ResourceFQDN>
      <Services><Service><Name>Pelican cache</Name></Service></Services>
    </Downtime>
  </CurrentDowntimes>
</Downtimes>
'''


class FakeResponse:
  def __init__(self, content=b'', status_code=200):
    self.content = content
    self.status_code = status_code

  def raise_for_status(self):
    if self.status_code != 200:
      raise requests.HTTPError(f'{self.status_code}')


class FakeWorksheet:
  # Just enough of gspread's Worksheet for process_worksheet
  def __init__(self, rows, statuses, notes=None):
    self.rows = rows
    self.cells = dict(statuses)
    self.notes = dict(notes or {})

  def get(self, cell_range):
    return self.rows

  def acell(self, cell):
    return types.SimpleNamespace(value=self.cells.get(cell))

  def update_acell(self, cell, value):
    self.cells[cell] = value

  def get_note(self, cell):
    return self.notes.get(cell, '')

  def update_note(self, cell, note):
    self.notes[cell] = note

  def clear_note(self, cell):
    self.notes.pop(cell, None)


def sheet_row(hostname, entry):
  # Columns B through J, hostname is B and factory entry is J
  return [hostname, '', '', '', '', '', '', '', entry]


@pytest.fixture
def topology(monkeypatch):
  # Serves DOWNTIME_XML by default, set .response to change what topology returns
  state = types.SimpleNamespace(response=FakeResponse(DOWNTIME_XML))

  def fake_get(url, timeout=None):
    assert url == main.DOWNTIME_URL
    if isinstance(state.response, Exception):
      raise state.response
    return state.response

  monkeypatch.setattr(main.requests, 'get', fake_get)
  return state


@pytest.fixture
def factory_calls(monkeypatch):
  # Factory is always unreachable, so rows that reach the factory check are recorded and skipped
  calls = []

  def fake_session_get(self, url):
    calls.append(url)
    return FakeResponse(status_code=404)

  monkeypatch.setattr(main.requests.Session, 'get', fake_session_get)
  monkeypatch.setattr(main.time, 'sleep', lambda seconds: None)
  return calls


def run(monkeypatch, worksheet):
  spreadsheet = types.SimpleNamespace(sheet1=worksheet)
  client = types.SimpleNamespace(open_by_key=lambda key: spreadsheet)
  monkeypatch.setattr(main.gspread, 'service_account_from_dict', lambda account: client)
  main.process_worksheet('old', 'new')


def test_get_downtime_hosts_only_current_ce_downtimes(topology):
  assert main.get_downtime_hosts() == {'down-ce1.example.org', 'multi-ce1.example.org'}


@pytest.mark.parametrize('response', [
  FakeResponse(status_code=500),
  FakeResponse(b'<html>not xml'),
  requests.ConnectionError('topology is down'),
])
def test_get_downtime_hosts_returns_none_when_topology_unavailable(topology, response):
  topology.response = response
  assert main.get_downtime_hosts() is None


@pytest.mark.parametrize('status', main.AUTOMATED_STATUSES)
def test_downtime_sets_maintenance(monkeypatch, topology, factory_calls, status):
  worksheet = FakeWorksheet([sheet_row('down-ce1.example.org ', 'DOWN_ENTRY')], {'C2': status})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == 'Maintenance'
  assert worksheet.cells['D2'] == status
  assert worksheet.notes['C2'] == main.DOWNTIME_NOTE
  assert factory_calls == []


@pytest.mark.parametrize('status', ['Maintenance', 'Retired'])
def test_downtime_leaves_manual_status(monkeypatch, topology, factory_calls, status):
  worksheet = FakeWorksheet([sheet_row('down-ce1.example.org', 'DOWN_ENTRY')], {'C2': status, 'D2': 'Production'})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == status
  assert worksheet.cells['D2'] == 'Production'
  assert worksheet.notes == {}


def test_downtime_ended_sets_production(monkeypatch, topology, factory_calls):
  worksheet = FakeWorksheet([sheet_row('up-ce1.example.org', 'UP_ENTRY')],
                            {'C2': 'Maintenance', 'D2': 'Production'}, {'C2': main.DOWNTIME_NOTE})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == 'Production'
  assert worksheet.cells['D2'] == 'Maintenance'
  assert worksheet.notes == {}
  assert factory_calls == []


def test_manual_maintenance_without_downtime_is_untouched(monkeypatch, topology, factory_calls):
  worksheet = FakeWorksheet([sheet_row('up-ce1.example.org', 'UP_ENTRY')], {'C2': 'Maintenance', 'D2': 'Broken'})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == 'Maintenance'
  assert worksheet.cells['D2'] == 'Broken'


def test_note_cleared_when_status_changed_by_hand(monkeypatch, topology, factory_calls):
  # Someone replaced the script's Maintenance after the downtime, so the row goes through the factory check
  worksheet = FakeWorksheet([sheet_row('up-ce1.example.org', 'UP_ENTRY')],
                            {'C2': 'Broken'}, {'C2': main.DOWNTIME_NOTE})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == 'Broken'
  assert worksheet.notes == {}
  assert factory_calls and all('entry_UP_ENTRY' in url for url in factory_calls)


def test_topology_unavailable_keeps_script_maintenance(monkeypatch, topology, factory_calls):
  topology.response = requests.ConnectionError('topology is down')
  worksheet = FakeWorksheet([sheet_row('down-ce1.example.org', 'DOWN_ENTRY'), sheet_row('up-ce1.example.org', 'UP_ENTRY')],
                            {'C2': 'Production', 'C3': 'Maintenance'}, {'C3': main.DOWNTIME_NOTE})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == 'Production'
  assert worksheet.cells['C3'] == 'Maintenance'
  assert worksheet.notes == {'C3': main.DOWNTIME_NOTE}


def test_rows_without_factory_entry_are_skipped(monkeypatch, topology, factory_calls):
  worksheet = FakeWorksheet([['down-ce1.example.org'], sheet_row('down-ce1.example.org', '  ')], {'C2': 'Production', 'C3': 'Production'})
  run(monkeypatch, worksheet)

  assert worksheet.cells['C2'] == 'Production'
  assert worksheet.cells['C3'] == 'Production'
  assert factory_calls == []
