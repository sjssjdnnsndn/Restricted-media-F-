import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Copy,
  ExternalLink,
  HeartPulse,
  LoaderCircle,
  Radio,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from 'lucide-react';

const queryClient = new QueryClient();

type HealthState = 'checking' | 'live' | 'unavailable';

function Home() {
  const [healthState, setHealthState] = useState<HealthState>('checking');
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [copied, setCopied] = useState(false);

  const endpoint = '/api/health';
  const publishedUrl = useMemo(() => window.location.origin, []);
  const healthUrl = `${publishedUrl}${endpoint}`;

  const checkHealth = useCallback(async () => {
    setHealthState('checking');
    try {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      const response = await fetch('/api/health', {
        signal: controller.signal,
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
      window.clearTimeout(timeout);
      if (!response.ok) throw new Error('Status request failed');
      setHealthState(response.ok ? 'live' : 'unavailable');
    } catch {
      setHealthState('unavailable');
    } finally {
      setLastChecked(new Date());
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  const copyEndpoint = async () => {
    try {
      await navigator.clipboard.writeText(healthUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2200);
    } catch {
      setCopied(false);
    }
  };

  const statusCopy = {
    checking: {
      label: 'Checking connection',
      description: 'Reaching the receiver status service now.',
      className: 'status-checking',
    },
    live: {
      label: 'Bot is running',
      description: 'The Telegram receiver is responding normally.',
      className: 'status-live',
    },
    unavailable: {
      label: 'Status unavailable',
      description: 'The status service could not be reached. Try again shortly.',
      className: 'status-unavailable',
    },
  }[healthState];

  return (
    <main className="min-h-[100dvh] overflow-hidden bg-background text-foreground">
      <div className="page-shell">
        <header className="topbar">
          <div className="brand-lockup" data-testid="text-product-name">
            <div className="brand-mark" aria-hidden="true">
              <Radio size={18} strokeWidth={2.3} />
            </div>
            <div>
              <div className="brand-name">File Receiver</div>
              <div className="brand-caption">Operations status</div>
            </div>
          </div>
          <div className="topbar-note">
            <span className="topbar-dot" aria-hidden="true" />
            private monitor
          </div>
        </header>

        <div className="content-grid">
          <section className="hero-panel" aria-labelledby="page-title">
            <div className="eyebrow"><span className="eyebrow-line" /> TELEGRAM BOT / 01</div>
            <h1 id="page-title">A quiet signal<br /><em>that it&apos;s alive.</em></h1>
            <p className="hero-copy">
              One place to confirm the file receiver is awake, connected, and ready to accept the next handoff.
            </p>
            <div className={`health-card ${statusCopy.className}`} data-testid="status-health">
              <div className="health-icon" aria-hidden="true">
                {healthState === 'checking' && <LoaderCircle className="spin" size={23} />}
                {healthState === 'live' && <CheckCircle2 size={23} />}
                {healthState === 'unavailable' && <CircleAlert size={23} />}
              </div>
              <div className="health-details">
                <div className="health-label">{statusCopy.label}</div>
                <div className="health-description" data-testid="text-status-description">{statusCopy.description}</div>
              </div>
              <button className="icon-button" onClick={() => void checkHealth()} aria-label="Check health again" data-testid="button-recheck">
                <RefreshCw size={17} />
              </button>
            </div>
            <div className="check-meta">
              <Clock3 size={14} />
              {lastChecked ? `Last checked ${lastChecked.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'First check in progress'}
            </div>
          </section>

          <aside className="signal-visual" aria-label="Receiver signal illustration">
            <div className="visual-grid" />
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <div className="signal-core">
              <div className="core-ring"><HeartPulse size={29} strokeWidth={1.6} /></div>
              <div className="core-pulse" />
            </div>
            <div className="visual-label visual-label-top"><span /> RECEIVER NODE</div>
            <div className="visual-label visual-label-bottom">NOMINAL <span className="mini-bars"><i /><i /><i /><i /></span></div>
            <div className="coordinate coordinate-one">43.6510° N<br />79.3470° W</div>
            <div className="coordinate coordinate-two">PING / 05 SEC</div>
          </aside>
        </div>

        <section className="lower-grid" aria-label="Monitoring details">
          <div className="endpoint-panel">
            <div className="section-kicker"><Terminal size={15} /> KEEP-ALIVE ENDPOINT</div>
            <div className="endpoint-row">
              <div className="endpoint-path" data-testid="text-health-endpoint"><span className="method">GET</span>{endpoint}</div>
              <button className="copy-button" onClick={() => void copyEndpoint()} data-testid="button-copy-endpoint">
                {copied ? <Check size={16} /> : <Copy size={16} />}
                {copied ? 'Copied' : 'Copy URL'}
              </button>
            </div>
            <p className="endpoint-note">A lightweight route for uptime checks. No authentication required.</p>
          </div>

          <div className="cron-panel">
            <div className="section-kicker"><ShieldCheck size={15} /> EXTERNAL MONITORING</div>
            <h2>Keep it warm from outside.</h2>
            <p>Use this published URL in your external cron service. Request <strong>/api/health</strong> on a regular interval to keep an eye on availability.</p>
            <div className="url-chip">
              <span className="url-chip-dot" />
              <span className="url-value" data-testid="text-published-url">{publishedUrl}</span>
              <a href={healthUrl} target="_blank" rel="noreferrer" aria-label="Open published app" data-testid="link-open-health"><ExternalLink size={15} /></a>
            </div>
          </div>
        </section>

        <footer className="footer">
          <div className="footer-status"><span className="footer-pulse" /> receiver monitor is active</div>
          <div className="footer-meta">File Receiver <span>·</span> single-bot operations</div>
        </footer>
      </div>
    </main>
  );
}

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;