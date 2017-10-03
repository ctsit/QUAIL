import datetime
from copy import copy

from quail.utils.quail_conf_util import QuailConfig
from quail.utils.redcap_util import redcap_batch
from quail.utils.redcap_util import redcap_metadata
from quail.utils.redcap_util import redcap_sqlize
from quail.db.factories import dynamic_schema, redcap_schema
from quail.utils.file_manipulation_mixin import FileManipulationMixin as file_util

def generate(quail_conf_path, name, token, url, init=False):
    """
    Generates a redcap project from which to pull data
    """
    print('Generating new redcap project')

    config = QuailConfig(quail_conf_path)
    quail_root = config.get_root()

    project_path = file_util.join([quail_root, 'sources', name])
    project_conf_path = file_util.join([project_path, 'redcap.conf.yaml'])
    project_batch_path = file_util.join([quail_root, 'batches', name])

    project_data = {
        'name': name,
        'token': token,
        'url': url,
        'batch_root': project_batch_path,
        'notes': {
            'source_type': 'Redcap',
            'free_text': ''
        }
    }
    config.add_source(name, project_data)

    file_util.mkdir(project_path)
    file_util.mkdir(project_batch_path)
    file_util.write(project_conf_path, project_data, 'yaml')
    config.save()
    print('Generated new redcap project {}'.format(name))
    if init:
        get_data(quail_conf_path, name, True)

def get_meta(quail_conf, project_name):
    """
    Gets the metadata for a particular redcap
    """
    print('Pulling metadata for {}'.format(project_name))
    config = QuailConfig(quail_conf)
    proj_data = config.get_source(project_name)
    del proj_data['notes']
    project = redcap_batch.Batcher(**proj_data)
    project.pull_metadata()
    print('Done pulling metadata for {}'.format(project_name))

def get_data(quail_conf, project_name, pull_metadata=False):
    """
    By default gets the metadata and data from an existing generated
    redcap project in the quail sources

    This function also adds the unique_field metadata to the quail.conf.yaml
    under the sources key.
    """
    config = QuailConfig(quail_conf)
    if pull_metadata:
        get_meta(quail_conf, project_name)
    print('Pulling data for {}'.format(project_name))

    start = datetime.datetime.now()
    proj_data = config.get_source(project_name)
    del proj_data['notes']
    project = redcap_batch.Batcher(**proj_data)
    project.pull_data()
    end = datetime.datetime.now()
    batch = {
        'project_name': project_name,
        'date': project.date,
        'metadata_date': project.metadata_date,
        'path': file_util.join([project.batch_root, project.date]),
        'start': str(start),
        'end': str(end),
    }
    file_util.write(file_util.join([batch['path'], 'batch_info.json']), batch, 'json')
    config.add_batch(project_name, project.date, batch)
    config.add_source_notes(project_name, 'unique_field', project.unique_field)
    config.save()
    print('Done pulling data for {}'.format(project_name))

def gen_meta(quail_conf, project_name):
    """
    Generates the metadata.db in the most recent batch folder of the project.
    This database contains information about how the redcap is set up
    """
    config = QuailConfig(quail_conf)
    most_recent_batch_path = config.get_most_recent_batch(project_name)

    database_path = file_util.join([most_recent_batch_path, 'metadata.db'])
    file_util.write(database_path)
    db = dynamic_schema(database_path)
    batch_size = 500

    arm = redcap_metadata.Arm(most_recent_batch_path, batch_size)
    event = redcap_metadata.Event(most_recent_batch_path, batch_size)
    instrument = redcap_metadata.Instrument(most_recent_batch_path, batch_size)
    instrument_event = redcap_metadata.InstrumentEvent(most_recent_batch_path, batch_size)
    field = redcap_metadata.Field(most_recent_batch_path, batch_size)
    project = redcap_metadata.Project(most_recent_batch_path, batch_size)

    db.create_table(**{
        'tables': [
            arm.table_data,
            event.table_data,
            instrument.table_data,
            instrument_event.table_data,
            field.table_data,
            project.table_data
        ]
    }).executescript()

    db.batch_insert(batches=arm.insert_data).executescript()
    db.commit()
    db.batch_insert(batches=event.insert_data).executescript()
    db.commit()
    db.batch_insert(batches=instrument.insert_data).executescript()
    db.commit()
    db.batch_insert(batches=instrument_event.insert_data).executescript()
    db.commit()
    db.batch_insert(batches=field.insert_data).executescript()
    db.commit()
    db.batch_insert(batches=project.insert_data).executescript()
    db.commit()

def gen_data(quail_conf, project_name):
    """
    Generates the data.db in the most recent batch folder of the project.
    This database contains all the data from the redcap data pull.

    Note that with python 3.4.2 there is a limit to the amount of rows
    that can be put in with a single insert into statement using the
    sqlite3 module which pyyesql uses under the covers. The error you
    will see is something about a compound select.

    Here the subjects are in a table along with tables of the forms.
    The database also has lookup tables for the dropdowns, checkboxes and
    radio buttons

    See docs/quail.org for a description of its schema
    """
    config = QuailConfig(quail_conf)
    most_recent_batch_path = config.get_most_recent_batch(project_name)

    redcap_data_path = file_util.join([most_recent_batch_path, 'redcap_data_files'])
    database_path = file_util.join([most_recent_batch_path, 'data.db'])
    file_util.write(database_path)
    db = redcap_schema(database_path)
    # in the future make the batch size configurable
    batch_size = 500

    print('Loading metadata...')
    instrumentor = redcap_sqlize.Instrumentor(most_recent_batch_path)
    subject_form = instrumentor.unique_field['form_name']

    print('Writing instruments tables...')
    for instrument in instrumentor.get_all_instruments():
        db.create_instrument(**instrument).execute()

    print('Writing checkboxes tables...')
    for checkbox in instrumentor.get_all_checkboxes():
        db.create_checkbox(**checkbox).executescript()

    print('Writing dropdowns tables...')
    for dropdown in instrumentor.get_all_dropdowns():
        db.create_lookup(**dropdown).executescript()

    print('Writing radios tables...')
    for radio in instrumentor.get_all_radios():
        db.create_lookup(**radio).executescript()

    db.close()
    print('Done with the schema starting with inserts...')

    db = dynamic_schema(database_path)

    for root, dirs, files in file_util.walk(redcap_data_path):
        for path in files:
            filepath = file_util.join([root, path])
            data = file_util.read(filepath, 'json')
            tablename = path.split('.')[0]

            table_info = db.table_info(table=tablename).execute().fetchall()
            name_index = 1
            is_pk_index = 5
            if tablename != subject_form:
                cols = [row[name_index] for row in table_info if not row[is_pk_index]]
            else:
                cols = [row[name_index] for row in table_info]
            cols.append('redcap_event_name')
            if type(data) != type([]):
                data = [data]
            empty_num = 0
            written_num = 0
            batches = []
            for item in data:
                if (not len(batches)) or (len(batches[-1]['vals']) == batch_size):
                    batches.append({
                        'tablename': tablename,
                        'cols': copy(cols),
                        'vals': []
                    })
                val = [str(item.setdefault(col, None)) for col in cols]
                val = [s.replace("\'","\'\'") if s != 'None' else '' for s in val]
                nonempty = [s for s in val if s != '']
                # every form will have a unique_field, redcap_event_name and form_complete
                # the subject_form will also specify the primary key
                required_fields = 3 if tablename == subject_form else 4
                if len(nonempty) > required_fields:
                    batches[-1]['vals'].append(val)
                    written_num += 1
                else:
                    empty_num += 1

            if len(batches[0]['vals']):
                db.batch_insert(batches=batches).executescript()
                db.commit()
            print('Wrote {} many rows to the {} table'.format(written_num, tablename))
            print('Redcap provided {} many empty records for {}'.format(empty_num, tablename))

    print('Done with inserting data')

