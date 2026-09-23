-- Nawaf Roblox Monitor — Studio Plugin
-- Replace the three configuration values below before installing/using the plugin.
-- Do NOT publish a plugin containing a real API key.

local HttpService = game:GetService("HttpService")
local LogService = game:GetService("LogService")

local API_URL = "https://YOUR-PUBLIC-DOMAIN.example.com/v1/errors"
local API_KEY = "PUT_YOUR_API_KEY_HERE"
local MAP_NAME_OVERRIDE = "" -- Optional. Leave empty to detect the current Roblox place automatically.

local SEND_COOLDOWN = 5
local lastSentAt = 0

local function getMapName()
	if MAP_NAME_OVERRIDE ~= "" then
		return MAP_NAME_OVERRIDE
	end

	local name = tostring(game.Name or "")
	if name ~= "" then
		return name
	end

	if game.PlaceId and game.PlaceId ~= 0 then
		return "Roblox Place " .. tostring(game.PlaceId)
	end

	return "Unknown Roblox Map"
end

local function sendError(message, messageType)
	if messageType ~= Enum.MessageType.MessageError then
		return
	end

	local now = os.clock()
	if now - lastSentAt < SEND_COOLDOWN then
		return
	end
	lastSentAt = now

	local payload = {
		map_name = getMapName(),
		message = tostring(message),
		message_type = tostring(messageType),
		source = "Roblox Studio",
		context = "Studio Output",
		place_id = tostring(game.PlaceId),
		game_id = tostring(game.GameId),
		timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ"),
	}

	task.spawn(function()
		local ok, response = pcall(function()
			return HttpService:RequestAsync({
				Url = API_URL,
				Method = "POST",
				Headers = {
					["Content-Type"] = "application/json",
					["X-API-Key"] = API_KEY,
				},
				Body = HttpService:JSONEncode(payload),
			})
		end)

		if not ok then
			warn("[Nawaf Roblox Monitor] HTTP request failed: " .. tostring(response))
			return
		end

		if not response.Success then
			warn(
				"[Nawaf Roblox Monitor] API returned "
					.. tostring(response.StatusCode)
					.. " "
					.. tostring(response.StatusMessage)
			)
		end
	end)
end

LogService.MessageOut:Connect(sendError)

print("[Nawaf Roblox Monitor] Active. Errors will be forwarded to the configured API.")
