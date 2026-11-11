# Market engine for the Agent Exchange.
# Every agent starts with the same coins. Each day it bets part of its coins on its
# forecast. The next day's price settles the bets, so agents that are right more often
# end up with more coins and more weight in the team forecast.


class Exchange:

    def __init__(self, agents, start_coins=100.0, cap=0.20, max_bet=0.10, min_coins=1.0):
        self.coins = {a: float(start_coins) for a in agents}
        self.cap = cap              # max share of the say for one agent
        self.max_bet = max_bet      # max share of its coins an agent can bet in one day
        self.min_coins = min_coins  # agents never drop to zero

    def weights(self, agents=None):
        agents = list(agents) if agents is not None else list(self.coins)
        total = sum(self.coins[a] for a in agents)
        w = {a: self.coins[a] / total for a in agents}

        # if there are too few agents the cap can't be met, so use equal weights
        if len(agents) * self.cap < 1:
            return {a: 1 / len(agents) for a in agents}

        # cap: cut anyone above the cap and share the extra among the others
        for _ in range(len(agents)):
            over = [a for a in agents if w[a] > self.cap + 1e-12]
            if not over:
                break
            extra = sum(w[a] - self.cap for a in over)
            for a in over:
                w[a] = self.cap
            under = [a for a in agents if w[a] < self.cap - 1e-12]
            under_total = sum(w[a] for a in under)
            for a in under:
                w[a] += extra * w[a] / under_total
        return w

    def team_forecast(self, forecasts):
        # forecasts: {agent_name: p_up}
        w = self.weights(forecasts.keys())
        return sum(w[a] * p for a, p in forecasts.items())

    def stake(self, agent, p_up):
        confidence = abs(2 * p_up - 1)   # 0 at 50%, 1 at 0% or 100%
        return self.max_bet * self.coins[agent] * confidence

    def settle(self, forecasts, went_up):
        results = {}
        for a, p in forecasts.items():
            if p == 0.5:
                results[a] = 0.0
                continue
            s = self.stake(a, p)
            right = (p > 0.5) == went_up
            change = s if right else -s
            self.coins[a] = max(self.min_coins, self.coins[a] + change)
            results[a] = change
        return results
