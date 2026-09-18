import json
import requests

with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

test_input = data['cases'][0]['input']

print("Sending request to local API...")
response = requests.post('https://ju-meandrous.onrender.com/optimize-energy', json=test_input)

if response.status_code == 200:
    print("Success! Here is the API response:")
    print(json.dumps(response.json(), indent=2))
else:
    print(f"Error {response.status_code}: {response.text}")