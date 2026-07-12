import { NextRequest, NextResponse } from 'next/server'

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { name, email, company, message } = body

    if (!name || !email || !message) {
      return NextResponse.json(
        { error: 'Name, email, and message are required' },
        { status: 400 }
      )
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!emailRegex.test(email)) {
      return NextResponse.json(
        { error: 'Invalid email address' },
        { status: 400 }
      )
    }

    // Log to console for now — replace with your email
    // provider (Resend, SendGrid, etc.) later
    console.log('Contact form submission:', {
      name, email, company, message,
      timestamp: new Date().toISOString()
    })

    return NextResponse.json(
      { success: true, message: 'Message received. We will be in touch within one business day.' },
      { status: 200 }
    )
  } catch {
    return NextResponse.json(
      { error: 'Something went wrong. Please try again.' },
      { status: 500 }
    )
  }
}
