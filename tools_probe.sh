p(){ printf "%-28s " "$1"; curl -s -m 15 -o /tmp/pb.txt -w "%{http_code} " "${@:2}"; head -c 140 /tmp/pb.txt | tr '\n' ' '; echo; }
p "OKX public"        https://www.okx.com/api/v5/public/time
p "OKX demo private"  -H "x-simulated-trading: 1" https://www.okx.com/api/v5/account/balance
p "Binance FutTestnet" https://testnet.binancefuture.com/fapi/v1/time
p "Binance FutTest pv" https://testnet.binancefuture.com/fapi/v2/balance
p "Bybit testnet"     https://api-testnet.bybit.com/v5/market/time
p "Bybit demo"        https://api-demo.bybit.com/v5/market/time
p "Bybit demo pv"     https://api-demo.bybit.com/v5/account/wallet-balance?accountType=UNIFIED
p "Kraken Fut demo"   https://demo-futures.kraken.com/derivatives/api/v3/tickers
p "Kraken Fut demo pv" https://demo-futures.kraken.com/derivatives/api/v3/accounts
p "Hyperliquid test"  -X POST -H "Content-Type: application/json" -d '{"type":"meta"}' https://api.hyperliquid-testnet.xyz/info
p "Hyperliquid ex"    -X POST -H "Content-Type: application/json" -d '{}' https://api.hyperliquid-testnet.xyz/exchange
p "Bitget public"     https://api.bitget.com/api/v2/public/time
p "IP-Land"           https://ipinfo.io/country
p "Binance FutTest symb" "https://testnet.binancefuture.com/fapi/v1/ticker/price?symbol=ADAUSDT"
p "Binance demo-fapi" https://demo-fapi.binance.com/fapi/v1/time
