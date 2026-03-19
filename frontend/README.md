# Automation Framework Frontend

This is the React-based frontend for the AI-Powered Record and Playback Automation Framework.

## Features

- **Dashboard**: Overview of automation activities and statistics
- **Recording Control**: Start/stop recording sessions with visual indicators
- **Flow Management**: View, edit, duplicate, and delete recorded flows
- **Playback Control**: Execute recorded flows with different playback modes
- **AI Data Generation**: Generate contextual test data using AI models
- **Configuration Panel**: Manage all framework settings in one place

## Technologies Used

- React 18
- Ant Design (AntD) for UI components
- Axios for API communication
- React Router for navigation
- React JSON View for data visualization

## Setup

1. Ensure Node.js is installed (version 14 or higher)
2. Navigate to the frontend directory:
   ```
   cd frontend
   ```
3. Install dependencies:
   ```
   npm install
   ```
4. Start the development server:
   ```
   npm start
   ```

## Building for Production

To create a production build:
```
npm run build
```

The build will be stored in the `build` directory.

## Development

The frontend is organized into the following structure:

```
src/
├── components/     # React components for each feature
├── App.js          # Main application component
├── index.js        # Entry point
└── App.css         # Global styles
```

## API Integration

The frontend communicates with the backend API at the following endpoints:
- `/record` - Recording management
- `/flows` - Flow management
- `/ai` - AI data generation
- `/config` - Configuration management

All API calls are handled through Axios with relative URLs.

## Customization

To customize the UI:
1. Modify components in the `src/components/` directory
2. Update styling in `src/App.css`
3. Add new routes in `src/App.js`

## Deployment

The frontend is designed to be served by the backend FastAPI application. When built, it will be served at the root path and at `/record-and-play` for the main dashboard.