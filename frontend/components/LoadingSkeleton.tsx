"use client";

import React from "react";

export function CardSkeleton() {
  return (
    <div className="glass-card p-6 animate-pulse">
      <div className="h-4 bg-surface-200 rounded w-1/3 mb-4"></div>
      <div className="h-8 bg-surface-200 rounded w-1/2 mb-2"></div>
      <div className="h-4 bg-surface-200 rounded w-1/4 mt-4"></div>
    </div>
  );
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="glass-card overflow-hidden animate-pulse">
      <div className="h-12 bg-surface-100 border-b border-surface-200"></div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-16 border-b border-surface-200 flex items-center px-6 gap-4">
          <div className="h-4 bg-surface-200 rounded w-1/4"></div>
          <div className="h-4 bg-surface-200 rounded w-1/4"></div>
          <div className="h-4 bg-surface-200 rounded w-1/4"></div>
          <div className="h-4 bg-surface-200 rounded w-1/4"></div>
        </div>
      ))}
    </div>
  );
}

export function ChatSkeleton() {
  return (
    <div className="flex flex-col gap-4 p-4 animate-pulse">
      <div className="flex gap-4">
        <div className="w-8 h-8 rounded-full bg-surface-200 shrink-0"></div>
        <div className="h-20 bg-surface-200 rounded-2xl rounded-tl-none w-2/3"></div>
      </div>
      <div className="flex gap-4 flex-row-reverse">
        <div className="w-8 h-8 rounded-full bg-brand-100 shrink-0"></div>
        <div className="h-12 bg-brand-50 rounded-2xl rounded-tr-none w-1/2"></div>
      </div>
      <div className="flex gap-4">
        <div className="w-8 h-8 rounded-full bg-surface-200 shrink-0"></div>
        <div className="h-16 bg-surface-200 rounded-2xl rounded-tl-none w-3/4"></div>
      </div>
    </div>
  );
}

export function GraphSkeleton() {
  return (
    <div className="glass-card p-8 h-64 animate-pulse flex items-center justify-between min-w-[800px]">
      {Array.from({ length: 5 }).map((_, i) => (
        <React.Fragment key={i}>
          <div className="w-16 h-16 rounded-2xl bg-surface-200 shrink-0"></div>
          {i < 4 && <div className="h-1 w-full bg-surface-100 mx-2"></div>}
        </React.Fragment>
      ))}
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="flex h-screen bg-background p-4 gap-4 animate-pulse">
      {/* Sidebar */}
      <div className="w-64 glass-card p-4 hidden md:flex flex-col gap-4">
        <div className="h-8 bg-surface-200 rounded w-3/4 mb-8"></div>
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-10 bg-surface-100 rounded w-full"></div>
        ))}
      </div>
      
      {/* Main Content */}
      <div className="flex-1 flex flex-col gap-4">
        {/* Header */}
        <div className="glass-card h-16 flex items-center justify-between px-6">
          <div className="h-6 bg-surface-200 rounded w-48"></div>
          <div className="w-8 h-8 rounded-full bg-surface-200"></div>
        </div>
        
        {/* Content */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
        
        <div className="flex-1 mt-4">
          <TableSkeleton rows={6} />
        </div>
      </div>
    </div>
  );
}
