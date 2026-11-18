"""Smooth, bounded cursor trajectories and travel timing (seconds/pixels)."""
import math


def travel_seconds(distance, square_size, requested):
    """Speed changes travel; a distance-aware floor prevents near-instant drags."""
    minimum = 0.14 + 0.04 * math.log2(1 + distance / max(1, square_size))
    return math.hypot(max(0, float(requested)), minimum)


def trajectory(points, duration, interval=0.008):
    """Resample (x,y,delay_ms) points using monotone cubic interpolation.

    Position remains inside each segment's coordinate bounds. Times are absolute
    offsets so callers can avoid accumulating sleep/dispatch overhead.
    """
    if not points:
        return []
    if len(points) == 1:
        return [(0.0, points[0][0], points[0][1])]
    times = [0.0]
    for point in points[1:]:
        times.append(times[-1] + max(0.001, float(point[2]) / 1000))
    total = times[-1]
    times = [t * duration / total for t in times]
    def slopes(axis):
        secants = [(points[i+1][axis]-points[i][axis])/(times[i+1]-times[i]) for i in range(len(points)-1)]
        result = [secants[0]]
        for a,b in zip(secants,secants[1:]):
            result.append(0.0 if a*b <= 0 else 2*a*b/(a+b))
        return result + [secants[-1]]
    mx,my = slopes(0),slopes(1)
    count = max(1, math.ceil(duration / interval))
    result = []
    segment = 0
    for i in range(count+1):
        elapsed = duration*i/count
        while segment < len(points)-2 and elapsed > times[segment+1]:
            segment += 1
        span = times[segment+1]-times[segment]
        u = (elapsed-times[segment])/span
        xy=[]
        for axis,m in ((0,mx),(1,my)):
            a,b=points[segment][axis],points[segment+1][axis]
            value=(2*u**3-3*u**2+1)*a+(u**3-2*u**2+u)*span*m[segment]+(-2*u**3+3*u**2)*b+(u**3-u**2)*span*m[segment+1]
            xy.append(max(min(a,b),min(max(a,b),value)))
        result.append((elapsed,*xy))
    return result
