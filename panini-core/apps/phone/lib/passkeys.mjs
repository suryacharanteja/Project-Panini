// WebAuthn buffers are serialized as base64url inside the encrypted fabric envelope.
export function decode(value) {
  const text = value.replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}

export function encode(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

export function options(json) {
  const result = { ...json, challenge: decode(json.challenge) };
  if (json.user) result.user = { ...json.user, id: decode(json.user.id) };
  for (const key of ['allowCredentials', 'excludeCredentials'])
    if (json[key]) result[key] = json[key].map((c) => ({ ...c, id: decode(c.id) }));
  return result;
}

export function serialize(credential) {
  const response = {};
  for (const key of [
    'clientDataJSON',
    'attestationObject',
    'authenticatorData',
    'signature',
    'userHandle',
  ])
    if (credential.response[key]) response[key] = encode(credential.response[key]);
  if (credential.response.getTransports) response.transports = credential.response.getTransports();
  return {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    response,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

function supported() {
  if (
    !globalThis.isSecureContext ||
    !globalThis.PublicKeyCredential ||
    !globalThis.navigator?.credentials
  )
    throw new Error('Passkeys require HTTPS and a supported browser.');
}

export async function enroll(fabric) {
  supported();
  const reply = await fabric.request('POST', '/passkeys/register/options', {
    origin: location.origin,
  });
  if (reply.status !== 200) throw new Error(reply.body?.detail || 'Passkey setup failed.');
  const credential = await navigator.credentials.create({ publicKey: options(reply.body.options) });
  if (!credential) throw new Error('Passkey setup was cancelled.');
  const saved = await fabric.request('POST', '/passkeys/register', {
    ceremony_id: reply.body.ceremony_id,
    credential: serialize(credential),
  });
  if (saved.status !== 200) throw new Error(saved.body?.detail || 'Passkey verification failed.');
}

export async function verify(fabric, shown, decision) {
  supported();
  const reply = await fabric.request('POST', `/interrupts/${shown.id}/passkey/options`, {
    ...decision,
    origin: location.origin,
  });
  if (reply.status !== 200) throw new Error(reply.body?.detail || 'Passkey approval failed.');
  const credential = await navigator.credentials.get({ publicKey: options(reply.body.options) });
  if (!credential) throw new Error('Passkey approval was cancelled.');
  return { ceremony_id: reply.body.ceremony_id, credential: serialize(credential) };
}
