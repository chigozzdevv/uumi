import uvicorn

if __name__ == "__main__":
    uvicorn.run("browser.gatewayapp:app", host="0.0.0.0", port=8080)
