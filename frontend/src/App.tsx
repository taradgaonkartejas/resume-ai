
function App() {
  return (
    <div className="grid h-dvh place-items-center bg-base">
      <div className="rounded-xl border border-line-soft bg-surface-1 p-8 text-center">
        <h1 className="text-2xl font-semibold text-content">Palette OK</h1>
        <p className="mt-2 text-sm text-content-muted">muted text</p>
        <button className="mt-4 rounded-lg bg-primary px-4 py-2 font-semibold text-white">
          Accept
        </button>
        <button className="mt-4 ml-2 rounded-lg bg-edit px-4 py-2 font-semibold text-base">
          Edit
        </button>
      </div>
    </div>
  );
}
export default App
