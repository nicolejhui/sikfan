const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

const config = getDefaultConfig(__dirname);

// react-native-gesture-handler@2.20.2 imports the legacy ReactNative renderer
// shim which was removed in react-native 0.76 (Fabric only). Redirect it to
// the Fabric renderer.
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === 'react-native/Libraries/Renderer/shims/ReactNative') {
    return {
      filePath: path.resolve(
        __dirname,
        'node_modules/react-native/Libraries/Renderer/shims/ReactFabric.js'
      ),
      type: 'sourceFile',
    };
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
