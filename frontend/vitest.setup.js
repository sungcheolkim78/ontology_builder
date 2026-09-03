// jsdom has no ResizeObserver -- DocumentPreview.vue uses one to measure its
// scroll container, which tests never actually need to observe.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverStub
