import React from 'react';
import { StepTimeline } from '../components/StepTimeline';
import './GroundOps.css';

const MOCK_STEPS = [
  { id: 's1', name: 'Open outer container', state: 'complete' as const, duration_s: 12.4 },
  { id: 's2', name: 'Remove red box', state: 'complete' as const, duration_s: 18.1 },
  { id: 's3', name: 'Remove yellow box', state: 'complete' as const, duration_s: 15.7 },
  { id: 's4', name: 'Open the red box', state: 'active' as const, duration_s: 4.2 },
  { id: 's5', name: 'Transfer vial', state: 'pending' as const },
  { id: 's6', name: 'Close and secure', state: 'pending' as const },
];

export const GroundOps: React.FC = () => {
  return (
    <div className="ground-ops">
      <div className="ops-header panel">
        <span>ORBITAL-HAR  PROC-A  SESSION 2026-09-20T13:24</span>
        <span className="live-dot">● LIVE</span>
      </div>

      <div className="ops-grid">
        <div className="ops-col-left">
          <div className="video-pane panel">
            <div className="video-placeholder">LIVE VIDEO + OVERLAYS</div>
            <div className="video-controls">
              <span>[ raw | canonical ]</span>
              <span>[ overlays ▾ ]</span>
            </div>
          </div>
          <div className="confidence-pane panel">
            <div className="pane-title">CONFIDENCE</div>
            <div className="conf-value mono">0.91</div>
            <div className="conf-thresholds">
              <div className="thresh">─────────────── τ complete</div>
              <div className="thresh">─────────────── τ abstain</div>
            </div>
          </div>
          <div className="health-pane panel">
            <div className="pane-title">HEALTH</div>
            <div className="health-metrics mono">22 FPS · 41ms · 3.1GB · RTX</div>
            <div className="health-dots">rack ● 4/4   cam ●   media ●</div>
          </div>
        </div>

        <div className="ops-col-right">
          <StepTimeline steps={MOCK_STEPS} />
          
          <div className="evidence-pane panel">
            <div className="pane-title">EVIDENCE — step 4</div>
            <div className="evidence-row">
              <span>detect red_box_open</span>
              <span className="mono">0.91 ✓</span>
            </div>
            <div className="evidence-row">
              <span>contact hand,red_box</span>
              <span className="mono">0.84 ✓</span>
            </div>
            <div className="evidence-row">
              <span>hold 12/12 frames</span>
              <span className="mono">✓</span>
            </div>
          </div>

          <div className="telemetry-pane panel">
            <div className="pane-title">TELEMETRY</div>
            <div className="telem-row"><span>written</span> <span className="mono">11.4 KB</span></div>
            <div className="telem-row"><span>raw video eq.</span> <span className="mono">1.94 GB</span></div>
            <div className="telem-row"><span>ratio</span> <span className="mono">170,000 : 1</span></div>
            <div className="telem-row"><span>chain</span> <span className="mono">✓ verified</span></div>
          </div>
        </div>
      </div>
    </div>
  );
};
