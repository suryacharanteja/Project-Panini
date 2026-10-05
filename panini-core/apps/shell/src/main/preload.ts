import { contextBridge, ipcRenderer } from 'electron';
const methods = [
  'status',
  'reconnect',
  'capabilities',
  'targets',
  'select',
  'turn',
  'stop',
  'end',
  'media',
  'voice-personas',
  'media-close',
  'dashboard',
  'admin',
  'feedback',
  'pair',
  'open-result',
  'window',
  'credentials',
  'execution-viewer',
  'execution-copy',
  'live',
  'continuity',
];
contextBridge.exposeInMainWorld(
  'panini',
  Object.freeze({
    invoke: (method: string, data?: unknown) => {
      if (!methods.includes(method)) return Promise.reject(new Error('Unsupported method'));
      return ipcRenderer.invoke('panini:invoke', method, data);
    },
    subscribe: (callback: (event: unknown) => void) => {
      const handler = (_event: unknown, value: unknown) => callback(value);
      ipcRenderer.on('panini:event', handler);
      return () => ipcRenderer.removeListener('panini:event', handler);
    },
    heartbeat: () => ipcRenderer.send('panini:heartbeat'),
  }),
);
