"""Placement heuristics propose the same KEEP/MOVE interface as IPPO."""


class PlacementBaseline:
    def __init__(self, method, config, hysteresis=.1):
        if method not in ("round_robin", "least_loaded"):
            raise ValueError("Unknown placement baseline")
        self.method, self.config, self.hysteresis = method, config, hysteresis
        self.cursor = [0, 1, 2]

    def act(self, env):
        masks, packet = env.masks(), env.packet()
        result = {}
        for i, name in enumerate(env.agents):
            valid = [c for c in range(3) if masks[name][c+1]]
            if not valid:
                result[name] = 0
                continue
            if self.method == "round_robin":
                order = [(self.cursor[i]+j) % 3 for j in range(3)]
                current = env.states[i].active
                target = next(c for c in order if c in valid or (c == current and env.available(c)))
                self.cursor[i] = (target+1) % 3
                if target == current:
                    result[name] = 0
                    continue
            else:
                def score(c):
                    cluster = packet["clusters"]["ABC"[c]]
                    # Current measured usage and reservations are alternative
                    # headroom signals; don't add them and double-count usage.
                    pressure = max(cluster["cpu_utilization"], cluster["cpu_reservation"], cluster["memory_utilization"], cluster["memory_reservation"])
                    return pressure + self.config.clusters[c].network_ms/1000
                target = min(valid, key=lambda c: (score(c), c))
                current = env.states[i].active
                if current is not None and env.available(current) and score(current)-score(target) <= self.hysteresis:
                    result[name] = 0
                    continue
            result[name] = target+1
        return result
