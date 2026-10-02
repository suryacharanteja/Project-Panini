import React from 'react';

/**
 * A card that cannot render is dropped, never the interface around it. Without this, one malformed event
 * unmounts the whole view; in the shell that also stops the liveness heartbeat (project memory).
 */
export class CardBoundary extends React.Component<
  { children: React.ReactNode; label?: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`A ${this.props.label || 'card'} failed to render and was hidden.`, error);
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
