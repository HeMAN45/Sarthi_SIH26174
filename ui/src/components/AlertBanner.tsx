import React from 'react';
import './AlertBanner.css';

export interface Alert {
  id: string;
  kind: 'skip' | 'out_of_order' | 'stall' | 'unverified' | 'free_float' | 'degraded';
  severity: 'low' | 'medium' | 'high';
  message: string;
}

interface AlertBannerProps {
  alert: Alert | null;
  onAcknowledge: (id: string) => void;
}

export const AlertBanner: React.FC<AlertBannerProps> = ({ alert, onAcknowledge }) => {
  if (!alert) return null;

  return (
    <div className={`alert-banner severity-${alert.severity}`}>
      <div className="alert-content">
        <span className="alert-icon">⚠</span>
        <div className="alert-text">
          <div className="alert-title">ALERT: {alert.kind.toUpperCase()}</div>
          <div className="alert-message">{alert.message}</div>
        </div>
      </div>
      {(alert.severity === 'high' || alert.severity === 'medium') && (
        <button className="alert-ack-btn" onClick={() => onAcknowledge(alert.id)}>
          Acknowledge
        </button>
      )}
    </div>
  );
};
