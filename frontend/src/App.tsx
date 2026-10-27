import { Component, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Home from './pages/Home'
import { AuthGate } from './auth'
import { PanelPage } from './components/memory/PanelPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, color: '#f87171', background: '#0c0e14', fontFamily: 'monospace', height: '100vh' }}>
          <h2 style={{ color: '#c8cdd8', marginBottom: 16 }}>AURORA crashed</h2>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{this.state.error.message}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 11, color: '#6b7280', marginTop: 12 }}>{this.state.error.stack}</pre>
          <button
            style={{ marginTop: 20, padding: '8px 16px', background: '#4f8ff7', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
            onClick={() => { this.setState({ error: null }); window.location.href = '/' }}
          >
            Go Home
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthGate>
            <Routes>
              <Route path="/panel" element={<PanelPage />} />
              <Route path="*" element={<Home />} />
            </Routes>
          </AuthGate>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
