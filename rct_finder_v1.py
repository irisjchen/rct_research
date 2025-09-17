from openai import OpenAI
import csv, json, re, time, requests

'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''
                          INSTRUCTIONS
    1. Current progress (number of RCTs processed) is tracked in
       checkpoint.txt. The program will automatically pick up where
       it left off when run multiple times
    2. Model output will be stored in csv format to results.csv
    3. It will be helpful to run the program in several smaller
       batches rather than all 3,000 at once. It may also be helpful
       to save intermediate versions of results.csv
    4. To restart, reset checkpoint.txt back to 0 and clear all lines 
       from results.csv
    5. Configure how many RCTs to process at once by setting
       MAX_RESULTS below

'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

''' Model Parameters '''
MAX_RESULTS = 5  # Number of RCTs to process
MODEL = "gpt-5-nano"  # OpenAI model to use
OPEN_AI_KEY = ''

''' IO Helpers '''

def load_checkpoint():
    with open('checkpoint.txt', 'r') as checkpoint_file:
        return int(checkpoint_file.readline())


def update_checkpoint(checkpoint):
    with open('checkpoint.txt', 'w') as checkpoint_file:
        checkpoint_file.write(str(checkpoint))


# Text expected to be in the format 'First Last example@email.com'
def extract_primary_investigator(raw_text):
    if '@' in raw_text:
        return " ".join(raw_text.split(' ')[:-1])
    return raw_text


# Text expected to be in the format 'First Last (example@email.com); First Last (example@email.com)'
def extract_other_primary_investigators(raw_text):
    investigators = raw_text.split(';')
    return list(map(lambda investigator: investigator.split('(')[0].strip(), investigators))


''' HTTP Helpers '''

def get_json(url, params=None, headers=None):
    for i in range(3):
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
        else:
            print(r.text)
        time.sleep(1 + i)
    return None


''' LLM Tool Definitions '''

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "crossref_search",
            "description": "Search Crossref by authors/keywords to find DOIs and publication metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_terms": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["search_terms"],
            }
        }
    }
]

''' LLM Tool Implementations '''


def crossref_filter(input_authors: list[str], all_results, max_results: int):
    input_authors = [author for author in input_authors if author.strip()]
    input_author_first_initial_last = set(
        map(lambda author: f"{author.lower().split(' ')[0][0]}. {author.lower().split(' ')[-1]}", input_authors))

    filtered_results = []
    for result in all_results:
        authors = result.get('author')
        try:
            authors_first_initial_last = set(
                map(lambda author: f"{author['given'].lower()[0]}. {author['family'].lower()}", authors))
            if input_author_first_initial_last & authors_first_initial_last:
                filtered_results.append(result)
        except:
            print(f"Error processing result, skipping... {result}")

    # Only return the top n results
    return filtered_results[:min(len(filtered_results), max_results)]


def crossref_deduplicate(all_results):
    seen_titles = set()
    deduplicated_results = []
    for result in all_results:
        if result['title'][0] not in seen_titles:
            seen_titles.add(result['title'][0])
            deduplicated_results.append(result)

    return deduplicated_results


def tool_crossref_search(authors: list[str], search_terms: list[str], country: str, rct_end_date: str):
    params = {
        "query.title": f'{" ".join(search_terms)} {country}',
        "rows": 20,
        "sort": "score",
        "order": "desc",
        "filter": f"type:journal-article,from-pub-date:{rct_end_date}",
        "mailto": "example@email.com",
    }

    all_results = []

    # Search first n authors individually
    for i in range(min(len(authors), 2)):
        # Only author last name
        author = authors[i]
        params['query.author'] = author.split(' ')[-1]
        response = get_json("https://api.crossref.org/works", params=params)
        all_results = all_results + response['message']['items']

    # Search all author last names at once, excluding search terms
    del params['query.title']
    params['query.author'] = ", ".join(list(map(lambda author: author.split(' ')[-1], authors)))
    response = get_json("https://api.crossref.org/works", params=params)
    all_results = all_results + response['message']['items']

    filtered_results = crossref_filter(authors, crossref_deduplicate(all_results), 50)
    print(f"Number of filtered results: {len(filtered_results)}")
    return filtered_results


''' Tool Dispatcher '''

def dispatch_tool(call, authors: list[str], country_names: str, rct_end_date: str):
    name = call.function.name
    args = call.function.arguments
    print(f'Using tool: {name} with parameters: {args}')
    try:
        # If the model sends a string map rather than an actual dict, attempt to process it anyway
        if type(args) == str:
            args = json.loads(args)
        match name:
            case "crossref_search":
                return tool_crossref_search(
                    # args['title'],
                    authors,
                    args['search_terms'],
                    country_names,
                    rct_end_date)
            case _:
                return {}
    except:
        raise


''' Logic for interacting with the LLM '''


def process_rct(
        client: OpenAI,
        checkpoint: int,
        title: str,
        rct_url: str,
        rct_id: str,
        authors: list[str],
        keywords: list[str],
        country_names: list[str],
        rct_end_date: str,
) -> str:
    print(f'Processing RCT #{checkpoint}: {title} by {", ".join(authors)}')

    system_prompt = '''
    You are an RCT publication finder using the RCT metadata provided by the user.

    Tool plan
    1) Given the title and keywords, extract the 6-9 most defining, high-signal search terms, preserving multi-word concepts (e.g., “unconditional cash transfers”) when needed, prioritizing distinctive nouns/phrases, removing stopwords/generic words and duplicates, and return them lowercased as a comma-separated list in priority order
    2) Use crossref_search to find the published article following the matching criteria:
        Authors — HARD requirement: if 1 or 2 authors, require ≥1 match. If ≥2 authors, require ≥50% match of last names, boost score for higher overlap
        Title/keyword tokens — REQUIRED overlap: require ≥4 token overlap between candidate title/abstract and search terms (handle variants like “unconditional cash transfer(s)”), or fuzzy title similarity ≥0.6
        Country: if Country_Names appears in candidate title/abstract/keywords (e.g., “Evidence from Kenya”), boost score
        Only keep type=journal-article results
        Exclude registry artifacts: exclude any DOI that has "10.1257/rct" in it or any URL on socialscienceregistry.org or any from AEA Randomized Controlled Trials journal
        Exclude working paper registries (E.g., SSRN)
        If the article is not found and tool calls aren't available, return 'null' for unknown fields
    '''

    # Input
    rct = {
        "rct_title": title,
        "rct_url": rct_url,
        "rct_id": rct_id,
        "authors": authors,
        "keywords": keywords,
        "country_names": country_names,
        "rct_end_date": rct_end_date,
    }

    # Output format
    schema = {
        "name": "publication_record",
        "schema": {
            "type": "object",
            "strict": True,
            "additionalProperties": False,
            "properties": {
                "rct_title": {'type': 'string'},
                "rct_id": {'type': 'string'},
                "doi": {"type": "string"},
                "title": {"type": "string"},
                "journal": {"type": "string"},
                "publisher_link": {"type": "string"},
            },
            "required": ["rct_title", "rct_id", "doi", "title", "journal", "publisher_link"]
        }
    }

    # Results from tool calls will be appended to the message context
    num_tool_calls = 0
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": str(rct)}]
    tool_call_messages = []
    while True:
        print(f"num_tool_calls: {num_tool_calls}, tool_choice: {"auto" if num_tool_calls < 2 else "none"}")
        response = client.chat.completions.create(
            model=MODEL,
            tools=TOOLS,
            tool_choice="auto" if num_tool_calls < 2 else "none",
            stream=False,
            messages=messages + tool_call_messages,
            response_format={
                "type": "json_schema",
                "json_schema": schema,
            }
        )

        msg = response.choices[0].message
        # always start a fresh message chain
        tool_call_messages = [msg]
        if msg.tool_calls:
            # Handle each tool call and feed the result back
            for call in msg.tool_calls:
                result = dispatch_tool(call, authors, " ".join(country_names), rct_end_date)
                tool_call_messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.function.name,
                    "content": str(result)
                })
            num_tool_calls += 1
        # If no more tool calls, return the final result
        else:
            # Spot check results
            data = json.loads(msg.content.strip())  # strict schema guarantees valid JSON
            try:
                row = [data["rct_id"], f'\"{data["rct_title"]}\"', data["doi"] or 'null', data["title"] or 'null', data["journal"] or 'null', data["publisher_link"] or 'null']
                print(f"Response: {row}")
                return ",".join(row)
            except:
                print(f"Error: {data}")
                row = [rct_id, title, 'null', 'null', 'null', 'null']
                return ",".join(row)


''' Method for managing logic and state '''
def find_publications_for_rcts(max_results: int):
    client = OpenAI(api_key=OPEN_AI_KEY)

    # Checkpoint so that the script can pick up where it left off if run multiple times
    checkpoint = load_checkpoint()
    results = 0
    with open('AEA_Complete_Only_2.csv', 'r', encoding="utf-8") as aea_complete, open('results_cref1_1.csv', 'a',
                                                                                      encoding="utf-8") as results_file:
        aea_reader = csv.DictReader(aea_complete)
        rows_to_skip = checkpoint
        for rct in aea_reader:
            # Skip over any rows that were previously processed
            if (rows_to_skip > 0):
                rows_to_skip -= 1
                continue

            # Stop if we have reached the maximum number of results requested
            if results >= max_results:
                return

            # Extract relevant fields to pass to the LLM
            title = rct['Title']
            rct_url = rct['Url']
            rct_id = rct['RCT_ID']
            primary_investigator = extract_primary_investigator(rct['Primary Investigator'])
            other_primary_investigators = extract_other_primary_investigators(rct['Other Primary Investigators'])
            authors = [primary_investigator] + other_primary_investigators
            keywords = rct['Keywords']
            country_names = rct['Country names'] if rct['Country names'] != "Private" else ""
            rct_end_date = rct['End date']

            # Delegate to LLM
            result = process_rct(client, checkpoint, title, rct_url, rct_id, authors, keywords, country_names,
                                 rct_end_date)

            # Update state
            results_file.write(result + "\n")
            checkpoint += 1
            update_checkpoint(checkpoint)
            results += 1


if __name__ == '__main__':

    find_publications_for_rcts(MAX_RESULTS)
