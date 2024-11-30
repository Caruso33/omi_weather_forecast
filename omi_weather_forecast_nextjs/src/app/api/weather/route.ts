import axios from "axios"
import { NextResponse, NextRequest } from "next/server"

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)

  const location = searchParams.get("location")

  if (!location) {
    return NextResponse.json({ error: "Location is required" }, { status: 400 })
  }

  try {
    const response = await axios.get(
      `https://maps.googleapis.com/maps/api/geocode/json?address=${encodeURIComponent(
        location as string
      )}&key=${process.env.GOOGLE_GEOCODING_API_KEY}`
    )

    const { results } = response.data
    if (!results.length) {
      return NextResponse.json({ error: "Location not found" }, { status: 400 })
    }

    const { geometry } = results[0]
    const { lat, lng } = geometry.location

    const openWeatherResponse = await axios.get(
      `https://api.openweathermap.org/data/2.5/forecast?lat=${lat}&lon=${lng}&units=metric&exclude=minutely,hourly&appid=${process.env.OPENWEATHER_API_KEY}`
    )

    if (openWeatherResponse.status !== 200) {
      return NextResponse.json(
        {
          error: openWeatherResponse.statusText,
        },
        { status: openWeatherResponse.status }
      )
    }

    const forecastData = openWeatherResponse.data?.list

    const currentWeather = forecastData[0]
    const dailyForecast = []

    for (let i = 0; i < forecastData.length; i += 8) {
      dailyForecast.push(forecastData[i])
    }

    const forecastText = generateForecastText(dailyForecast)

    return NextResponse.json(
      { lat, lng, current: currentWeather, forecastText },
      { status: 200 }
    )
  } catch (error) {
    console.error(error)
    return NextResponse.json(
      { error: "Internal Server Error" },
      { status: 500 }
    )
  }
}

const generateForecastText = (dailyForecast: unknown[]) => {
  const forecastText: unknown[] = []

  dailyForecast.forEach((day: any) => {
    const date = new Date(day.dt * 1000).toLocaleDateString("en-US", {
      weekday: "long",
      month: "long",
      day: "numeric",
    })

    const temperature = `${day.main.temp}°C`
    const weatherDescription = day.weather?.[0].description

    forecastText.push(`${date}: ${weatherDescription}, high of ${temperature}`)
  })

  return forecastText.join(". ")
}
