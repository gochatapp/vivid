import requests

# Query the native/OpenAI-compatible models endpoint on llama-server
try:
    response = requests.get("http://localhost:8080/v1/models")
    if response.status_code == 200:
        models_data = response.json()
        print("Available models in llama.cpp:")
        for model in models_data.get("data", []):
            print(f"- {model.get('id')}")
    else:
        print(f"Failed to fetch models, status code: {response.status_code}")
        print(f"Response: {response.text}")
except Exception as e:
    print(f"Could not connect to server: {e}")