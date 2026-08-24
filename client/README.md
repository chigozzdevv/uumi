# FireKey client

The FireKey operator dashboard is a React, TypeScript, Tailwind CSS 4, and Vite application. It consumes the same organisation-scoped HTTP routes exposed by the Python API.

## Run locally

```sh
npm ci
cp .env.example .env.local
npm run dev
```

Run the Python API separately on `http://127.0.0.1:8000`. Vite proxies `/v1` and `/health` to that API by default; change `FIREKEY_DEV_API_URL` only when the local API uses a different origin.

The Firebase web values configure Google sign-in. `VITE_API_URL` may remain empty when the dashboard and API share an origin. Every API request includes the signed-in user's Firebase ID token; the Python API verifies the token and organisation membership.

## Verify

```sh
npm run check
```

The check runs lint, type-checks the client, and produces a production build.
