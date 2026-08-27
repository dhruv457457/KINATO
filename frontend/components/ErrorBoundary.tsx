"use client";

import React, { Component, ErrorInfo, ReactNode, useState, useCallback } from "react";
import { AlertTriangle, RefreshCcw, X } from "lucide-react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught error:", error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  public render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="min-h-[400px] w-full flex items-center justify-center p-6">
          <div className="glass-card bg-surface-50 max-w-md w-full p-8 flex flex-col items-center text-center">
            <div className="w-16 h-16 bg-red-100 text-red-600 rounded-full flex items-center justify-center mb-6">
              <AlertTriangle className="w-8 h-8" />
            </div>
            <h2 className="text-xl font-bold text-dark font-serif mb-2">Something went wrong</h2>
            <p className="text-surface-600 text-sm mb-8 break-words w-full">
              {this.state.error?.message || "An unexpected error occurred."}
            </p>
            <button
              onClick={this.handleReset}
              className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white px-6 py-2.5 rounded-xl font-medium transition-colors shadow-lg shadow-brand-600/20"
            >
              <RefreshCcw className="w-4 h-4" />
              Try Again
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * Hook for catching async errors in functional components and triggering the ErrorBoundary
 */
export function useErrorHandler() {
  const [, setError] = useState();
  return useCallback(
    (e: Error) => {
      setError(() => {
        throw e;
      });
    },
    [setError]
  );
}

/**
 * Inline alert component for displaying non-fatal API errors
 */
export function ApiErrorAlert({ 
  error, 
  onRetry, 
  onDismiss 
}: { 
  error: string | null; 
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  if (!error) return null;

  return (
    <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3">
      <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
      <div className="flex-1">
        <h4 className="text-sm font-medium text-red-800">Error</h4>
        <p className="text-sm text-red-600 mt-1">{error}</p>
        {onRetry && (
          <button 
            onClick={onRetry}
            className="text-sm font-medium text-red-700 hover:text-red-800 mt-3 flex items-center gap-1"
          >
            <RefreshCcw className="w-3 h-3" /> Retry
          </button>
        )}
      </div>
      {onDismiss && (
        <button onClick={onDismiss} className="text-red-400 hover:text-red-600 p-1">
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
