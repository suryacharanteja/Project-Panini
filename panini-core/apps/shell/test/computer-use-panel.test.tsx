import { describe, expect, it } from 'vitest';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { ComputerUsePanel, PanelBoundary, describeActivity } from '../src/renderer/ComputerUsePanel';

const panel = (events: any[]) =>
  renderToString(React.createElement(ComputerUsePanel, { events, phase: 'running' }));

describe('the live computer-use panel never breaks the interface', () => {
  it('renders the first "observed" event of a desktop task, whose action is an empty object', () => {
    // This exact payload used to throw ("Objects are not valid as a React child"), unmounting the
    // avatar, stopping its heartbeat and making PANINI lose control of the task.
    const html = panel([
      { phase: 'observed', message: 'Observed the selected Windows application.', action: {}, surface: 'desktop' },
    ]);
    expect(html).toContain('Observed');
    expect(html).toContain('Observed the selected Windows application.');
  });

  it('renders every action shape the backend can send', () => {
    const html = panel([
      { phase: 'observed', action: {} },
      { phase: 'proposed', action: { kind: 'click', x: 10, y: 20 }, message: 'Click Pencil' },
      { phase: 'proposed', action: { kind: 'draw', strokes: 8, points: 200 }, message: 'Draw' },
      { phase: 'dispatched', action: 'a plain string' },
      { phase: 'step', action: null, message: 'No action' },
      { phase: 'error', message: { nested: 'object' } },
      { phase: { odd: true } },
    ]);
    expect(html).toContain('click');
    expect(html).toContain('draw');
    expect(html).toContain('a plain string');
    expect(html).toContain('No action');
  });

  it('describes activity as text only', () => {
    expect(describeActivity({ action: {}, message: 'Looking' })).toBe('Looking');
    expect(describeActivity({ action: {} })).toBe('Activity');
    expect(describeActivity({ action: { kind: 'double_click' } })).toBe('double click');
    expect(describeActivity({ action: 'typed' })).toBe('typed');
    expect(describeActivity({ message: 'only a message' })).toBe('only a message');
    expect(describeActivity(undefined)).toBe('Activity');
    for (const value of [{}, { action: {} }, { action: [] }, { message: {} }, { action: { kind: {} } }])
      expect(typeof describeActivity(value)).toBe('string');
  });

  it('draws the cursor mark only for numeric coordinates', () => {
    expect(panel([{ phase: 'proposed', action: { x: 5, y: 6 } }])).toContain('computer-use-cursor');
    expect(panel([{ phase: 'proposed', action: { x: '5', y: 6 } }])).not.toContain('computer-use-cursor');
  });

  it('shows nothing without events', () => {
    expect(panel([])).toBe('');
  });
});

describe('a panel that cannot render is hidden instead of taking the interface down', () => {
  it('catches a render failure and renders nothing', () => {
    expect(PanelBoundary.getDerivedStateFromError()).toEqual({ failed: true });
    const boundary = new PanelBoundary({ children: React.createElement('p', null, 'panel') });
    expect(boundary.render()).toBeTruthy();
    boundary.state = { failed: true };
    expect(boundary.render()).toBeNull();
  });
});
