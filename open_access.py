import csv, time, requests
import pprint

'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''
                          INSTRUCTIONS
    1. Current progress (number of Publications processed) is tracked in
       <CHECKPOINT_FILE>. The program will automatically pick up where
       it left off when run multiple times
    2. Model output will be stored in csv format to <RESULTS_FILE>
    3. Configure how many RCTs to process at once by setting
       <MAX_RESULTS> below

'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

''' Model Parameters '''
MAX_RESULTS = 1  # Number of publications to process
PUBLICATION_INPUT_FILE = 'rct_results.csv'
CHECKPOINT_FILE = "open_access_checkpoint.txt"
RESULTS_FILE = "open_access_results.txt"

''' IO Helpers '''
def load_checkpoint():
    with open(CHECKPOINT_FILE, 'r') as checkpoint_file:
        return int(checkpoint_file.readline())

def update_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, 'w') as checkpoint_file:
        checkpoint_file.write(str(checkpoint))

''' HTTP Helpers '''
def get_json(url, params=None, headers=None):
    max_attempts = 3
    for attempt in range(max_attempts):
        r = requests.get(url, params=params, headers=headers, timeout=60)
        if r.status_code == 200:
            return r.json()
        elif attempt ==  max_attempts - 1:
            print(r.text)
        time.sleep(attempt)
    return None

''' Publication Search Helpers '''
def try_get(valueProvider):
    try:
        value = valueProvider()
        return str(value) if value is not None else 'null'
    except:
        return 'null'

def search_open_alex(doi: str):
    params = { "mailto": "irisch317@gmail.com" }
    response = get_json(f"https://api.openalex.org/works/https://doi.org/{doi}", params=params)
    pprint.pprint(response)
    return response

def find_publication_for_doi(doi: str):
    response = search_open_alex(doi)
    csv_values = [
        doi,
        try_get(lambda: response['apc_list']['value']),
        try_get(lambda: response['apc_list']['currency']),
        try_get(lambda: response['apc_paid']),
        try_get(lambda: response['best_oa_location']),
        try_get(lambda: response['cited_by_count']),
        try_get(lambda: response['open_access']['is_oa']),
        try_get(lambda: response['open_access']['oa_status']),
        try_get(lambda: response['open_access']['oa_url']),
        try_get(lambda: response['publication_date']),
        try_get(lambda: response['publication_year']),
        try_get(lambda: response['primary_location']['is_oa']),
        try_get(lambda: response['primary_location']['license']),
        try_get(lambda: response['primary_location']['license_id']),
        try_get(lambda: response['primary_location']['landing_page_url']),
        try_get(lambda: response['primary_location']['pdf_url']),
        try_get(lambda: response['primary_location']['raw_source_name']),
        try_get(lambda: response['primary_location']['source']['host_organization_name']),
        try_get(lambda: response['primary_location']['source']['is_in_doaj']),
        try_get(lambda: response['primary_location']['source']['is_oa']),
    ]
    for location in response['locations']:
        csv_values.append(try_get(lambda: location['is_oa']))
        csv_values.append(try_get(lambda: location['license']))
        csv_values.append(try_get(lambda: location['license_id']))
        csv_values.append(try_get(lambda: location['landing_page_url']))
        csv_values.append(try_get(lambda: location['pdf_url']))
        csv_values.append(try_get(lambda: location['raw_source_name']))
        csv_values.append(try_get(lambda: location['source']['host_organization_name']))
        csv_values.append(try_get(lambda: location['source']['is_in_doaj']))
        csv_values.append(try_get(lambda: location['source']['is_oa']))
    return csv_values

def find_open_publications(max_results: int):
    # Checkpoint so that the script can pick up where it left off if run multiple times
    checkpoint = load_checkpoint()
    results = 0
    with open(PUBLICATION_INPUT_FILE, 'r', encoding="utf-8") as publication_file, open(RESULTS_FILE, 'a', encoding="utf-8") as results_file:
        rct_reader = csv.reader(publication_file)
        rows_to_skip = checkpoint
        for rct in rct_reader:
            # Skip over any rows that were previously processed
            if (rows_to_skip > 0):
                rows_to_skip -= 1
                continue

            # Stop if we have reached the maximum number of results requested
            if results >= max_results:
                return

            # Find publication
            doi = rct[2]
            publication_results = find_publication_for_doi(doi)

            # Update state
            results_file.write(','.join(publication_results) + '\n')
            checkpoint += 1
            update_checkpoint(checkpoint)
            results += 1

if __name__ == '__main__':
    find_open_publications(MAX_RESULTS)