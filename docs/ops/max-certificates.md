# MAX certificates and TLS diagnosis

The plugin keeps TLS verification enabled. `MAX_CA_BUNDLE` is an optional
per-client PEM bundle and must be managed outside this repository.

## Policy

- Do not set `MAX_CA_BUNDLE` to `false`, `0`, `none` or `insecure`.
- Do not use `verify=False` in a local smoke or deployment.
- A configured bundle must exist, be readable as ASCII PEM, and contain a
  non-empty `CERTIFICATE` block.
- Keep the system roots and the current official chain required by the MAX API
  endpoint in the deployment-managed bundle.
- Record the bundle path, SHA-256 fingerprint, date and source in the operator
  change log. Do not commit the bundle itself.

## Diagnosis order

Run checks from the Hermes host and classify the first failing layer:

1. DNS resolution of `platform-api2.max.ru`.
2. TCP connection to port 443.
3. TLS handshake with the system trust store.
4. TLS handshake with `MAX_CA_BUNDLE`.
5. HTTPS `/me`, distinguishing `401` from transport failure.
6. Webhook subscription and delivery, if Webhook mode is configured.

A `401` without a token is evidence that DNS/TCP/HTTP reached the endpoint; it
does not validate the bot token or prove that a mobile-network restriction will
allow the endpoint.

## Deployment example

```env
MAX_CA_BUNDLE=/etc/hermes/max-ca-bundle.pem
```

The same policy applies to the optional Webhook ingress. TLS termination for
public port 443 belongs to the reverse proxy; the ingress should receive only
traffic from that proxy or a network boundary with equivalent controls.
