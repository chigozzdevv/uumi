# FireKey client

The FireKey operator dashboard is a React, TypeScript, Tailwind CSS 4, and Vite application. It consumes the same organisation-scoped HTTP routes exposed by the Python API.

## Run locally

```sh
npm install
npm run dev
```

The command starts Vite and the local mock API together. Vite proxies `/v1` and `/health` to `http://127.0.0.1:8787`, so the client uses relative API paths in both local and deployed environments.

## Verify

```sh
npm run check
```

The check validates the mock graph and lifecycle relationships, runs lint, type-checks the client, and produces a production build.

The mock server contains metadata and references only. It does not contain or return plaintext credential material.
