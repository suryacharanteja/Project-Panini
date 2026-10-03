// IndexedDB for the phone app: the device's non-extractable CryptoKeys, the PC's public keys and drafts.
// CryptoKey objects are structured-cloneable, so private keys are stored without ever being exported.
const NAME = 'panini-phone';
const STORE = 'kv';

function open() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run(mode, fn) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const request = fn(tx.objectStore(STORE));
    tx.oncomplete = () => {
      db.close();
      resolve(request?.result);
    };
    tx.onabort = tx.onerror = () => {
      db.close();
      reject(tx.error || new Error('Storage failed.'));
    };
  });
}

export const get = (key) => run('readonly', (s) => s.get(key));
export const set = (key, value) => run('readwrite', (s) => s.put(value, key));
export const remove = (key) => run('readwrite', (s) => s.delete(key));
